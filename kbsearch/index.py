"""磁盘倒排索引：建库、打开、增量增删。

索引目录布局（细节见 README）：
    meta.json          版本、N、总长度、avgdl、BM25 参数、停用词
    slots.bin          定长槽位：每内部编号 8 字节（存活状态 + 文档长度）
    live.bitmap        存活内部编号位图（NOT / 集合补集用，不随结果集入内存）
    stopwords.txt      建库时停用词表副本
    terms/xx/yyyy..    每个词项一个内容寻址倒排文件（df/tf/位置）
    docs/xx/xxxxxxxx   每个内部编号一个文档清单：外部编号 + 词项列表
"""

import json
import os

from .codec import (
    Bitmap,
    SLOT_SIZE,
    append_vint,
    pack_slot,
    read_vint,
    unpack_slot,
)
from .postings import merge_posting, read_posting, term_path
from .tokenizer import Tokenizer, load_stopwords

META_NAME = "meta.json"
SLOTS_NAME = "slots.bin"
LIVE_NAME = "live.bitmap"
STOP_NAME = "stopwords.txt"
TERMS_DIR = "terms"
DOCS_DIR = "docs"
INDEX_VERSION = 1
K1 = 1.2
B = 0.75


class IndexError_(RuntimeError):
    pass


def _doc_path(root, internal):
    name = "%08x" % internal
    return os.path.join(root, DOCS_DIR, name[:2], name)


def _write_doc_record(root, internal, doc_id, terms):
    payload = bytearray()
    encoded = doc_id.encode("utf-8")
    append_vint(payload, len(encoded))
    payload += encoded
    append_vint(payload, len(terms))
    for term in sorted(terms):
        tb = term.encode("utf-8")
        append_vint(payload, len(tb))
        payload += tb
    path = _doc_path(root, internal)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(b"CA01")
        fh.write(payload)
    os.replace(tmp, path)


def _read_doc_record(path):
    with open(path, "rb") as fh:
        magic = fh.read(4)
        if magic != b"CA01":
            raise ValueError("文档清单文件损坏：%s" % path)
        name_len = read_vint(fh)
        doc_id = fh.read(name_len).decode("utf-8")
        count = read_vint(fh)
        terms = []
        for _ in range(count):
            tlen = read_vint(fh)
            terms.append(fh.read(tlen).decode("utf-8"))
    return doc_id, terms


def _iter_doc_records(root):
    base = os.path.join(root, DOCS_DIR)
    if not os.path.isdir(base):
        return
    for shard in sorted(os.listdir(base)):
        sdir = os.path.join(base, shard)
        if not os.path.isdir(sdir):
            continue
        for name in sorted(os.listdir(sdir)):
            if name.endswith(".tmp"):
                continue
            yield os.path.join(sdir, name)


class _ReaderBase:
    def __init__(self, root):
        self.root = root
        with open(os.path.join(root, META_NAME), "r", encoding="utf-8") as fh:
            self.meta = json.load(fh)
        self.tokenizer = Tokenizer(set(self.meta["stopwords"]))
        self.terms_root = os.path.join(root, TERMS_DIR)
        self._slots_fh = open(os.path.join(root, SLOTS_NAME), "rb")
        self._live = Bitmap.load(os.path.join(root, LIVE_NAME))

    def close(self):
        self._slots_fh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def n_docs(self):
        return self.meta["n_docs"]

    @property
    def avgdl(self):
        return self.meta["avgdl"]

    def doc_length(self, internal_id):
        self._slots_fh.seek(internal_id * SLOT_SIZE)
        raw = self._slots_fh.read(SLOT_SIZE)
        if len(raw) < SLOT_SIZE:
            raise IndexError_("内部文档编号越界：%d" % internal_id)
        alive, length = unpack_slot(raw)
        return length if alive else 0

    def external_id(self, internal_id):
        return _read_doc_record(_doc_path(self.root, internal_id))[0]

    def live_ids(self):
        return self._live.ids()

    def is_live(self, internal_id):
        return self._live.get(internal_id)

    def posting_for_term(self, term):
        _, postings = read_posting(term_path(self.terms_root, term))
        return {d: p for d, p in postings.items() if self._live.get(d)}


class Index:
    def __init__(self, root):
        self.root = root

    @classmethod
    def create(cls, root, stopwords_file):
        os.makedirs(root, exist_ok=True)
        os.makedirs(os.path.join(root, TERMS_DIR), exist_ok=True)
        os.makedirs(os.path.join(root, DOCS_DIR), exist_ok=True)
        stopwords = sorted(load_stopwords(stopwords_file))
        with open(os.path.join(root, STOP_NAME), "w", encoding="utf-8") as fh:
            fh.write("\n".join(stopwords))
        meta = {
            "version": INDEX_VERSION,
            "n_docs": 0,
            "total_length": 0,
            "avgdl": 0.0,
            "k1": K1,
            "b": B,
            "stopwords": stopwords,
        }
        cls._write_meta(root, meta)
        open(os.path.join(root, SLOTS_NAME), "wb").close()
        Bitmap(0).save(os.path.join(root, LIVE_NAME))
        return cls(root)

    @classmethod
    def _write_meta(cls, root, meta):
        tmp = os.path.join(root, META_NAME + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, os.path.join(root, META_NAME))

    def writer(self):
        return Writer(self.root)

    def searcher(self):
        from .searcher import Searcher
        return Searcher(self.root)


class Writer(_ReaderBase):
    """单写者：单篇增删即时提交；add_documents 批量提交（建库/批量导入）。"""

    def __init__(self, root):
        super().__init__(root)
        self._id_map = None
        self._slots_fh.close()
        self._slots_fh = open(os.path.join(root, SLOTS_NAME), "r+b")

    def close(self):
        self._slots_fh.close()

    def _ensure_id_map(self):
        if self._id_map is not None:
            return
        mapping = {}
        for path in _iter_doc_records(self.root):
            internal = int(os.path.basename(path), 16)
            if self._live.get(internal):
                doc_id, _ = _read_doc_record(path)
                mapping[doc_id] = internal
        self._id_map = mapping

    def internal_id(self, doc_id):
        self._ensure_id_map()
        return self._id_map.get(doc_id)

    def add_document(self, doc_id, text):
        self.add_documents([(doc_id, text)])

    def delete_document(self, doc_id):
        return self.delete_documents([doc_id])

    def add_documents(self, docs):
        """(doc_id, text) 可迭代；同一 doc_id 重复导入按替换处理。"""
        self._ensure_id_map()
        adds = {}       # internal -> {"id","terms","length"}
        deletes = {}    # internal -> set(旧词项)
        pending = set()    # 本次批次新占用的内部编号
        free = set()       # 当前可用的死槽
        for internal in self._dead_slots():
            free.add(internal)
        next_new = self._slots_fh.seek(0, os.SEEK_END) // SLOT_SIZE
        for doc_id, text in docs:
            words = self.tokenizer.tokenize(text)
            term_positions = {}
            for idx, word in enumerate(words, start=1):
                term_positions.setdefault(word, []).append(idx)
            existing = self._id_map.get(doc_id)
            if existing is not None and existing not in adds:
                _, old_terms = _read_doc_record(_doc_path(self.root, existing))
                deletes[existing] = set(old_terms)
            if existing is not None:
                internal = existing
            elif free:
                internal = min(free)
                free.discard(internal)
                pending.add(internal)
            else:
                internal = next_new
                next_new += 1
                pending.add(internal)
            adds[internal] = {
                "id": doc_id,
                "terms": term_positions,
                "length": len(words),
            }
        self._commit(adds, deletes)

    def delete_documents(self, doc_ids):
        self._ensure_id_map()
        deletes = {}
        for doc_id in doc_ids:
            internal = self._id_map.get(doc_id)
            if internal is None or internal in deletes:
                continue
            _, old_terms = _read_doc_record(_doc_path(self.root, internal))
            deletes[internal] = set(old_terms)
        if not deletes:
            return False
        self._commit({}, deletes)
        return True

    def _dead_slots(self):
        total = self._slots_fh.seek(0, os.SEEK_END) // SLOT_SIZE
        for internal in range(total):
            if not self._live.get(internal):
                yield internal

    def _commit(self, adds, deletes):
        term_adds = {}
        term_deletes = {}
        length_delta = 0
        n_delta = 0

        # 1) 旧文档：从词项增量中剔除。被替换的文档编号在 adds 中复用，
        #    既不减 N 也不改存活位；纯删除才减 N、置死槽。
        for internal, termset in deletes.items():
            length_delta -= self._read_slot_length(internal)
            if internal not in adds:
                n_delta -= 1
                self._live.set(internal, False)
            for term in termset:
                term_deletes.setdefault(term, set()).add(internal)

        # 2) 新文档（含替换）：聚合词项增量，写槽位与文档清单。
        for internal, info in adds.items():
            if internal not in deletes:
                n_delta += 1  # 替换的文档在删除阶段已减过，这里不再计
            length_delta += info["length"]
            self._live.set(internal, True)
            self._write_slot(internal, True, info["length"])
            _write_doc_record(self.root, internal, info["id"], info["terms"].keys())
            for term, positions in info["terms"].items():
                term_adds.setdefault(term, {})[internal] = positions

        # 3) 只重写受影响词项的倒排文件。
        for term in set(term_adds) | set(term_deletes):
            merge_posting(
                self.terms_root,
                term,
                term_adds.get(term, {}),
                term_deletes.get(term, set()),
            )

        # 4) 删除文档的清单与外部编号映射。
        for internal in deletes:
            if internal in adds:
                continue
            path = _doc_path(self.root, internal)
            try:
                doc_id, _ = _read_doc_record(path)
            except FileNotFoundError:
                doc_id = None
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            if doc_id is not None:
                self._id_map.pop(doc_id, None)
        for internal, info in adds.items():
            self._id_map[info["id"]] = internal

        # 5) 位图、槽位、元数据落盘（avgdl 增量更新，不全量重建）。
        self._live.save(os.path.join(self.root, LIVE_NAME))
        self._slots_fh.flush()
        os.fsync(self._slots_fh.fileno())
        meta = dict(self.meta)
        meta["n_docs"] = self.meta["n_docs"] + n_delta
        meta["total_length"] = self.meta["total_length"] + length_delta
        meta["avgdl"] = (
            meta["total_length"] / meta["n_docs"] if meta["n_docs"] > 0 else 0.0
        )
        Index._write_meta(self.root, meta)
        self.meta = meta

    def _read_slot_length(self, internal):
        self._slots_fh.seek(internal * SLOT_SIZE)
        raw = self._slots_fh.read(SLOT_SIZE)
        if len(raw) < SLOT_SIZE:
            return 0
        _, length = unpack_slot(raw)
        return length

    def _write_slot(self, internal, alive, length):
        self._slots_fh.seek(internal * SLOT_SIZE)
        self._slots_fh.write(pack_slot(alive, length))
