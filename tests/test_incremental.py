import os
import shutil
import tempfile
import unittest

from searchlib import IndexWriter, SearchEngine, load_stopwords

ROOT = os.path.join(os.path.dirname(__file__), "..")
SAMPLES = os.path.join(ROOT, "samples")
STOPWORDS_PATH = os.path.join(SAMPLES, "stopwords.txt")
DOCS_DIR = os.path.join(SAMPLES, "docs")
QUERIES = [
    "检索",
    '"倒排索引"',
    "索引 AND 文档",
    "检索 AND NOT 慢",
    "(倒排索引 OR 扫描) AND 文档",
    "NOT 检索",
]


def read_docs():
    docs = {}
    for name in sorted(os.listdir(DOCS_DIR)):
        if name.endswith(".txt"):
            with open(os.path.join(DOCS_DIR, name), encoding="utf-8") as f:
                docs[name[:-4]] = f.read()
    return docs


def build(index_dir, docs):
    os.makedirs(index_dir, exist_ok=True)
    shutil.copyfile(STOPWORDS_PATH, os.path.join(index_dir, "stopwords.txt"))
    writer = IndexWriter(index_dir, load_stopwords(STOPWORDS_PATH))
    for doc_id, text in docs.items():
        writer.add_document(doc_id, text)
    writer.commit()


class IncrementalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.docs = read_docs()

    def tearDown(self):
        shutil.rmtree(self._tmp)

    def _results(self, index_dir):
        engine = SearchEngine(index_dir)
        return {q: engine.search(q) for q in QUERIES}

    def test_delete_matches_full_rebuild(self):
        inc = os.path.join(self._tmp, "inc")
        build(inc, self.docs)
        writer = IndexWriter(inc, load_stopwords(os.path.join(inc, "stopwords.txt")))
        writer.delete_document("doc-02")
        writer.delete_document("doc-09")
        writer.commit()

        remaining = {k: v for k, v in self.docs.items()
                     if k not in ("doc-02", "doc-09")}
        full = os.path.join(self._tmp, "full")
        build(full, remaining)

        self.assertEqual(self._results(inc), self._results(full))

    def test_add_matches_full_rebuild(self):
        subset = {k: v for k, v in self.docs.items() if k != "doc-04"}
        inc = os.path.join(self._tmp, "inc")
        build(inc, subset)
        writer = IndexWriter(inc, load_stopwords(os.path.join(inc, "stopwords.txt")))
        writer.add_document("doc-04", self.docs["doc-04"])
        writer.commit()

        full = os.path.join(self._tmp, "full")
        build(full, self.docs)

        self.assertEqual(self._results(inc), self._results(full))

    def test_delete_then_readd_roundtrip(self):
        inc = os.path.join(self._tmp, "inc")
        build(inc, self.docs)
        before = self._results(inc)
        writer = IndexWriter(inc, load_stopwords(os.path.join(inc, "stopwords.txt")))
        writer.delete_document("doc-07")
        writer.commit()
        writer = IndexWriter(inc, load_stopwords(os.path.join(inc, "stopwords.txt")))
        writer.add_document("doc-07", self.docs["doc-07"])
        writer.commit()
        self.assertEqual(self._results(inc), before)

    def test_delete_unknown_raises(self):
        inc = os.path.join(self._tmp, "inc")
        build(inc, self.docs)
        writer = IndexWriter(inc, load_stopwords(os.path.join(inc, "stopwords.txt")))
        with self.assertRaises(KeyError):
            writer.delete_document("no-such-doc")

    def test_add_duplicate_raises(self):
        inc = os.path.join(self._tmp, "inc")
        build(inc, self.docs)
        writer = IndexWriter(inc, load_stopwords(os.path.join(inc, "stopwords.txt")))
        with self.assertRaises(ValueError):
            writer.add_document("doc-01", "anything")

    def test_only_affected_shards_change(self):
        inc = os.path.join(self._tmp, "inc")
        build(inc, self.docs)
        postings_dir = os.path.join(inc, "postings")
        before = {
            name: os.path.getmtime(os.path.join(postings_dir, name))
            for name in os.listdir(postings_dir)
        }
        import time
        time.sleep(0.01)
        writer = IndexWriter(inc, load_stopwords(os.path.join(inc, "stopwords.txt")))
        writer.delete_document("doc-01")
        writer.commit()
        changed = {
            name for name in os.listdir(postings_dir)
            if os.path.getmtime(os.path.join(postings_dir, name)) != before[name]
        }
        # 只重写 doc-01 的词命中的 shard，而不是全部 256 个
        self.assertTrue(changed)
        self.assertLess(len(changed), 50)


if __name__ == "__main__":
    unittest.main()
