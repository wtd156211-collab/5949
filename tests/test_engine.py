import math
import os
import shutil
import tempfile
import unittest

from searchlib import IndexWriter, SearchEngine, load_stopwords

ROOT = os.path.join(os.path.dirname(__file__), "..")
SAMPLES = os.path.join(ROOT, "samples")
STOPWORDS_PATH = os.path.join(SAMPLES, "stopwords.txt")
DOCS_DIR = os.path.join(SAMPLES, "docs")


def build_index(index_dir, docs_dir=DOCS_DIR):
    os.makedirs(index_dir, exist_ok=True)
    shutil.copyfile(STOPWORDS_PATH, os.path.join(index_dir, "stopwords.txt"))
    writer = IndexWriter(index_dir, load_stopwords(STOPWORDS_PATH))
    for name in sorted(os.listdir(docs_dir)):
        if not name.endswith(".txt"):
            continue
        doc_id = name[: -len(".txt")]
        with open(os.path.join(docs_dir, name), encoding="utf-8") as f:
            writer.add_document(doc_id, f.read())
    writer.commit()


class SampleAcceptanceTest(unittest.TestCase):
    """samples/queries.txt + samples/expected.tsv 是验收标准。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        cls.index_dir = os.path.join(cls._tmp, "index")
        build_index(cls.index_dir)
        cls.engine = SearchEngine(cls.index_dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp)

    def test_expected_order(self):
        with open(os.path.join(SAMPLES, "expected.tsv"), encoding="utf-8") as f:
            cases = [line.rstrip("\n").split("\t") for line in f if line.strip()]
        self.assertTrue(cases)
        for row in cases:
            query, expected = row[0], row[1] if len(row) > 1 else ""
            want = [d for d in expected.split(",") if d]
            with self.subTest(query=query):
                self.assertEqual(self.engine.search(query), want)

    def test_deterministic_across_runs(self):
        # 同一份数据跑两遍，结果必须一模一样
        other = os.path.join(self._tmp, "index2")
        build_index(other)
        engine2 = SearchEngine(other)
        with open(os.path.join(SAMPLES, "queries.txt"), encoding="utf-8") as f:
            queries = [line.rstrip("\n") for line in f if line.strip()]
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(self.engine.search(query), engine2.search(query))
                self.assertEqual(self.engine.search(query), self.engine.search(query))


class ScoringTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.index_dir = os.path.join(self._tmp, "index")
        os.makedirs(self.index_dir)
        shutil.copyfile(STOPWORDS_PATH, os.path.join(self.index_dir, "stopwords.txt"))
        self.writer = IndexWriter(self.index_dir, load_stopwords(STOPWORDS_PATH))

    def tearDown(self):
        shutil.rmtree(self._tmp)

    def _commit_and_open(self, docs):
        for doc_id, text in docs:
            self.writer.add_document(doc_id, text)
        self.writer.commit()
        return SearchEngine(self.index_dir)

    def test_bm25_value(self):
        engine = self._commit_and_open([
            ("d1", "alpha alpha beta"),
            ("d2", "alpha"),
        ])
        # 手算 d1 的 alpha 得分
        n, df, tf, dl = 2, 2, 2, 3
        avgdl = 2.0
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        expect = idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * dl / avgdl))
        matched, scores = engine._eval(engine._stopwords and __import__(
            "searchlib.query", fromlist=["parse"]).parse("alpha", engine._stopwords))
        self.assertAlmostEqual(scores["d1"], expect)

    def test_shorter_doc_ranks_higher(self):
        engine = self._commit_and_open([
            ("d1", "alpha " + "beta " * 50),
            ("d2", "alpha beta"),
        ])
        self.assertEqual(engine.search("alpha"), ["d2", "d1"])

    def test_tie_break_by_doc_id(self):
        engine = self._commit_and_open([
            ("doc-b", "alpha"),
            ("doc-a", "alpha"),
            ("doc-c", "alpha"),
        ])
        self.assertEqual(engine.search("alpha"), ["doc-a", "doc-b", "doc-c"])

    def test_phrase_weight_and_consecutive(self):
        engine = self._commit_and_open([
            ("d1", "alpha beta gamma"),      # 短语命中
            ("d2", "beta alpha gamma"),      # 顺序反了，不命中
            ("d3", "alpha gamma beta"),      # 不连续，不命中
        ])
        self.assertEqual(engine.search('"alpha beta"'), ["d1"])
        # 单词查询三个都命中
        self.assertEqual(sorted(engine.search("alpha beta")), ["d1", "d2", "d3"])

    def test_phrase_df_uses_phrase_hits(self):
        engine = self._commit_and_open([
            ("d1", "alpha beta"),
            ("d2", "alpha beta"),
            ("d3", "alpha gamma beta"),
        ])
        # 短语 df=2（d1,d2），不是单词的 df=3
        matched, scores = engine._eval(__import__(
            "searchlib.query", fromlist=["parse"]).parse('"alpha beta"', engine._stopwords))
        n, df = 3, 2
        idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
        expect = 2.0 * idf * (1 * 2.2) / (1 + 1.2 * (0.25 + 0.75 * 2 / (7 / 3)))
        self.assertAlmostEqual(scores["d1"], expect)

    def test_not_only_filters(self):
        engine = self._commit_and_open([
            ("d1", "alpha beta"),
            ("d2", "alpha"),
            ("d3", "beta"),
        ])
        self.assertEqual(engine.search("alpha AND NOT beta"), ["d2"])
        self.assertEqual(engine.search("NOT alpha"), ["d3"])

    def test_empty_query_matches_nothing(self):
        engine = self._commit_and_open([("d1", "alpha")])
        self.assertEqual(engine.search("the"), [])
        self.assertEqual(engine.search(""), [])
        self.assertEqual(engine.search("!!!"), [])


if __name__ == "__main__":
    unittest.main()
