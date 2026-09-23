"""倒排索引的落盘布局与增量更新。

索引目录结构：

    index_dir/
        meta.json            {"num_docs": N, "total_len": 全部文档词元数之和}
        docs.json            {doc_id: 文档词元数}，NOT 查询的全集也从这里来
        terms.json           {term: df}
        stopwords.txt        建索引时拷贝的停用词表，保证查询口径一致
        postings/xx.json     256 个 shard，按 term 的 sha1 首字节分片：
                             {term: [[doc_id, doc_len, [pos, ...]], ...]}
        forward/xx.json      256 个 shard，按 doc_id 的 sha1 首字节分片：
                             {doc_id: {term: [pos, ...]}}

doc_len 冗余在每条倒排记录里，查询打分只需要查询词所在 shard 的数据，
内存占用不随文档总数增长。新增/删除文档只重写该文档的词所命中的
postings shard 和该文档所在的 forward shard，不做全量重建。
"""

import hashlib
import json
import os

from .tokenizer import tokenize

_SHARDS = 256


def _shard_of(key):
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:2]


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _dump_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    os.replace(tmp, path)


class IndexWriter:
    """增量构建/更新索引。批量操作后调用 commit() 落盘。"""

    def __init__(self, index_dir, stopwords=frozenset()):
        self._dir = index_dir
        self._stopwords = stopwords
        self._postings_dir = os.path.join(index_dir, "postings")
        self._forward_dir = os.path.join(index_dir, "forward")
        os.makedirs(self._postings_dir, exist_ok=True)
        os.makedirs(self._forward_dir, exist_ok=True)
        self._meta = _load_json(
            os.path.join(index_dir, "meta.json"), {"num_docs": 0, "total_len": 0}
        )
        self._docs = _load_json(os.path.join(index_dir, "docs.json"), {})
        self._terms = _load_json(os.path.join(index_dir, "terms.json"), {})
        self._postings_cache = {}
        self._forward_cache = {}
        self._dirty_postings = set()
        self._dirty_forward = set()

    def _postings_shard(self, shard):
        if shard not in self._postings_cache:
            self._postings_cache[shard] = _load_json(
                os.path.join(self._postings_dir, shard + ".json"), {}
            )
        return self._postings_cache[shard]

    def _forward_shard(self, shard):
        if shard not in self._forward_cache:
            self._forward_cache[shard] = _load_json(
                os.path.join(self._forward_dir, shard + ".json"), {}
            )
        return self._forward_cache[shard]

    def add_document(self, doc_id, text):
        if doc_id in self._docs:
            raise ValueError("文档已存在: %r" % doc_id)
        terms = {}
        doc_len = 0
        for pos, tok in enumerate(tokenize(text, self._stopwords), 1):
            terms.setdefault(tok, []).append(pos)
            doc_len += 1

        fshard = _shard_of(doc_id)
        self._forward_shard(fshard)[doc_id] = terms
        self._dirty_forward.add(fshard)

        for term, positions in terms.items():
            pshard = _shard_of(term)
            shard = self._postings_shard(pshard)
            shard.setdefault(term, []).append([doc_id, doc_len, positions])
            self._dirty_postings.add(pshard)
            self._terms[term] = self._terms.get(term, 0) + 1

        self._docs[doc_id] = doc_len
        self._meta["num_docs"] += 1
        self._meta["total_len"] += doc_len

    def delete_document(self, doc_id):
        if doc_id not in self._docs:
            raise KeyError("文档不存在: %r" % doc_id)
        fshard = _shard_of(doc_id)
        forward = self._forward_shard(fshard)
        terms = forward.pop(doc_id)
        self._dirty_forward.add(fshard)

        for term in terms:
            pshard = _shard_of(term)
            shard = self._postings_shard(pshard)
            entries = shard[term]
            shard[term] = [e for e in entries if e[0] != doc_id]
            if not shard[term]:
                del shard[term]
            self._dirty_postings.add(pshard)
            remaining = self._terms[term] - 1
            if remaining:
                self._terms[term] = remaining
            else:
                del self._terms[term]

        doc_len = self._docs.pop(doc_id)
        self._meta["num_docs"] -= 1
        self._meta["total_len"] -= doc_len

    def commit(self):
        for shard in self._dirty_postings:
            data = self._postings_cache[shard]
            for term in data:
                data[term].sort(key=lambda e: e[0])
            _dump_json(os.path.join(self._postings_dir, shard + ".json"), data)
        for shard in self._dirty_forward:
            _dump_json(
                os.path.join(self._forward_dir, shard + ".json"),
                self._forward_cache[shard],
            )
        self._dirty_postings.clear()
        self._dirty_forward.clear()
        _dump_json(os.path.join(self._dir, "terms.json"), self._terms)
        _dump_json(os.path.join(self._dir, "docs.json"), self._docs)
        _dump_json(os.path.join(self._dir, "meta.json"), self._meta)


class IndexReader:
    """只读打开索引，按需加载 shard，内存占用不随文档数增长。"""

    def __init__(self, index_dir):
        self._dir = index_dir
        self._postings_dir = os.path.join(index_dir, "postings")
        self._meta = _load_json(
            os.path.join(index_dir, "meta.json"), {"num_docs": 0, "total_len": 0}
        )
        self._terms = None
        self._shard_cache = {}

    @property
    def num_docs(self):
        return self._meta["num_docs"]

    @property
    def avgdl(self):
        n = self._meta["num_docs"]
        return self._meta["total_len"] / n if n else 0.0

    def df(self, term):
        if self._terms is None:
            self._terms = _load_json(os.path.join(self._dir, "terms.json"), {})
        return self._terms.get(term, 0)

    def postings(self, term):
        """返回 [[doc_id, doc_len, [pos, ...]], ...]，按 doc_id 升序。"""
        shard = _shard_of(term)
        if shard not in self._shard_cache:
            self._shard_cache[shard] = _load_json(
                os.path.join(self._postings_dir, shard + ".json"), {}
            )
        return self._shard_cache[shard].get(term, [])

    def all_doc_ids(self):
        docs = _load_json(os.path.join(self._dir, "docs.json"), {})
        return sorted(docs)
