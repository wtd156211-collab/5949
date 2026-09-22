import math
import os
import shutil
import tempfile
import time
import unittest

from kbsearch.index import Index
from kbsearch.searcher import Searcher

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "samples")
STOPWORDS = os.path.join(SAMPLES, "stopwords.txt")
DOCS = os.path.join(SAMPLES, "docs")
QUERIES = os.path.join(SAMPLES, "queries.txt")
EXPECTED = os.path.join(SAMPLES, "expected.tsv")


class IndexTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kbidx_test_")
        self.dir = os.path.join(self.tmp, "idx")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build_samples(self):
        Index.create(self.dir, STOPWORDS)
        batch = []
        for name in sorted(os.listdir(DOCS)):
            with open(os.path.join(DOCS, name), encoding="utf-8") as fh:
                batch.append((name[:-4], fh.read()))
        with Index(self.dir).writer() as writer:
            writer.add_documents(batch)
        return batch


class AcceptanceTest(IndexTestBase):
    def test_matches_expected_tsv(self):
        self.build_samples()
        with open(QUERIES, encoding="utf-8") as fh:
            queries = [line.rstrip("\n") for line in fh if line.strip()]
        with open(EXPECTED, encoding="utf-8") as fh:
            expected = {}
            for line in fh:
                q, ids = line.rstrip("\n").split("\t")
                expected[q] = ids.split(",") if ids else []
        with Index(self.dir).searcher() as searcher:
            for query in queries:
                got = [doc_id for doc_id, _ in searcher.search(query)]
                self.assertEqual(got, expected[query], msg=query)


class PhraseTest(IndexTestBase):
    def build(self):
        Index.create(self.dir, STOPWORDS)
        docs = {
            "d1": "alpha beta gamma x alpha beta",  # "alpha beta" 两次
            "d2": "alpha x beta",
            "d3": "beta alpha",                    # 顺序反了
            "d4": "state of the art system",       # 停用词不占位
            "d5": "state the art of",              # state, art 同样连续
        }
        with Index(self.dir).writer() as writer:
            writer.add_documents(list(docs.items()))

    def test_adjacency_and_order(self):
        self.build()
        with Index(self.dir).searcher() as s:
            self.assertEqual([d for d, _ in s.search('"alpha beta"')], ["d1"])

    def test_stopwords_not_occupy_positions(self):
        self.build()
        with Index(self.dir).searcher() as s:
            ids = [d for d, _ in s.search('"state of the art"')]
            self.assertEqual(sorted(ids), ["d4", "d5"])
            self.assertEqual(ids, ["d5", "d4"])  # d5 更短，tf 相同分更高

    def test_phrase_tf_counts_overlapping_hits(self):
        self.build()
        with Index(self.dir).searcher() as s:
            results = dict(s.search('"alpha"'))
            self.assertIn("d1", results)
            # d1 里 alpha 出现两次
            self.assertGreater(results["d1"], 0.0)

    def test_phrase_with_no_hit(self):
        self.build()
        with Index(self.dir).searcher() as s:
            self.assertEqual(s.search('"gamma alpha"'), [])

    def test_phrase_terms_not_scored_separately(self):
        # 短语命中的得分必须是短语整体贡献，不能拆成单词之和。
        self.build()
        with Index(self.dir).searcher() as s:
            phrase_score = dict(s.search('"alpha beta"'))["d1"]
            # 手工按公式：df=1，d1 中短语连续命中 tf=2
            idf = math.log(1 + (5 - 1 + 0.5) / (1 + 0.5))
            tf = 2
            dl = 6
            avgdl = 3.2
            k1, b = 1.2, 0.75
            expect = 2.0 * idf * (tf * (k1 + 1)) / (
                tf + k1 * (1 - b + b * dl / avgdl))
            self.assertAlmostEqual(phrase_score, expect, places=9)


class BooleanTest(IndexTestBase):
    def test_and_or_not_parentheses(self):
        Index.create(self.dir, STOPWORDS)
        docs = {
            "a": "red fish pond",
            "b": "blue fish lake",
            "c": "red bird sky",
            "d": "green tree slow",
        }
        with Index(self.dir).writer() as writer:
            writer.add_documents(list(docs.items()))
        with Index(self.dir).searcher() as s:
            self.assertEqual([d for d, _ in s.search("red AND fish")], ["a"])
            red_or_blue = [d for d, _ in s.search("red OR blue")]
            self.assertEqual(red_or_blue[0], "b")
            self.assertEqual(sorted(red_or_blue), ["a", "b", "c"])
            self.assertEqual(
                [d for d, _ in s.search("fish AND NOT red")], ["b"])
            both = [d for d, _ in s.search("(red OR blue) AND fish")]
            self.assertEqual(sorted(both), ["a", "b"])
            self.assertEqual(both[0], "b")  # blue 更罕见，得分更高
            not_red = [d for d, _ in s.search("NOT red")]
            self.assertEqual(sorted(not_red), ["b", "d"])
            self.assertEqual(
                sorted(d for d, _ in s.search("NOT NOT red")), ["a", "c"])
            self.assertEqual(
                sorted(d for d, _ in s.search("NOT (red AND fish)")),
                ["b", "c", "d"])

    def test_empty_query_returns_nothing(self):
        Index.create(self.dir, STOPWORDS)
        with Index(self.dir).writer() as writer:
            writer.add_documents([("x", "hello world")])
        with Index(self.dir).searcher() as s:
            self.assertEqual(s.search("the"), [])
            self.assertEqual(s.search(""), [])
            self.assertEqual(s.search('"the of"'), [])

    def test_unknown_term(self):
        Index.create(self.dir, STOPWORDS)
        with Index(self.dir).writer() as writer:
            writer.add_documents([("x", "hello world")])
        with Index(self.dir).searcher() as s:
            self.assertEqual(s.search("nope"), [])
            self.assertEqual([d for d, _ in s.search("hello OR nope")], ["x"])


class ScoringTest(IndexTestBase):
    def test_tf_and_length_normalization(self):
        Index.create(self.dir, STOPWORDS)
        # d1: cat 两次、更短；d2: cat 一次、更长
        docs = {"d1": "cat cat dog", "d2": "cat dog emu fox owl"}
        with Index(self.dir).writer() as writer:
            writer.add_documents(list(docs.items()))
        with Index(self.dir).searcher() as s:
            ids = [d for d, _ in s.search("cat")]
            self.assertEqual(ids, ["d1", "d2"])

    def test_tie_break_by_doc_id_bytes(self):
        Index.create(self.dir, STOPWORDS)
        docs = {"d1": "identical content here",
                "d2": "identical content here"}
        with Index(self.dir).writer() as writer:
            writer.add_documents(list(docs.items()))
        with Index(self.dir).searcher() as s:
            results = s.search("identical")
            ids = [d for d, _ in results]
            self.assertEqual(ids, ["d1", "d2"])
            self.assertEqual(results[0][1], results[1][1])

    def test_not_does_not_add_score(self):
        Index.create(self.dir, STOPWORDS)
        docs = {"a": "red fish", "b": "red grass"}
        with Index(self.dir).writer() as writer:
            writer.add_documents(list(docs.items()))
        with Index(self.dir).searcher() as s:
            plain = dict(s.search("red"))
            with_not = dict(s.search("red AND NOT fish"))
            self.assertAlmostEqual(with_not["b"], plain["b"], places=12)
            self.assertNotIn("a", with_not)


class IncrementalTest(IndexTestBase):
    def _make(self):
        Index.create(self.dir, STOPWORDS)

    def test_add_then_query(self):
        self._make()
        with Index(self.dir).writer() as writer:
            writer.add_document("d1", "hello alpha world")
        with Index(self.dir).searcher() as s:
            self.assertEqual([d for d, _ in s.search("alpha")], ["d1"])

    def test_delete_only_touches_affected_parts(self):
        self._make()
        with Index(self.dir).writer() as writer:
            writer.add_documents([
                ("d1", "alpha beta"),
                ("d2", "alpha gamma"),
                ("d3", "beta gamma"),
            ])
        terms_before = {}
        tdir = os.path.join(self.dir, "terms")
        for shard in os.listdir(tdir):
            for name in os.listdir(os.path.join(tdir, shard)):
                terms_before[os.path.join(shard, name)] = os.path.getsize(
                    os.path.join(tdir, shard, name))
        with Index(self.dir).writer() as writer:
            self.assertTrue(writer.delete_document("d2"))
            self.assertFalse(writer.delete_document("d2"))
        with Index(self.dir).searcher() as s:
            self.assertEqual(
                [d for d, _ in s.search("alpha")], ["d1"])
            self.assertEqual(
                [d for d, _ in s.search("gamma")], ["d3"])
        # 只有 alpha / gamma 两个词项文件允许变化，beta 不变。
        changed = []
        for rel, size in terms_before.items():
            path = os.path.join(tdir, rel)
            now = os.path.getsize(path) if os.path.exists(path) else None
            if now != size:
                changed.append(rel)
        self.assertEqual(len(changed), 2)

    def test_reuse_dead_slot_on_readd(self):
        self._make()
        with Index(self.dir).writer() as writer:
            writer.add_documents([
                ("d1", "alpha"), ("d2", "beta"), ("d3", "gamma")])
            writer.delete_document("d2")
            writer.add_document("d4", "delta")
            slots_size = os.path.getsize(os.path.join(self.dir, "slots.bin"))
            self.assertEqual(slots_size, 3 * 8)  # 复用 d2 槽位，未扩展
        with Index(self.dir).searcher() as s:
            self.assertEqual([d for d, _ in s.search("delta")], ["d4"])
            self.assertEqual(s.search("beta"), [])
            self.assertEqual(s.n_docs, 3)

    def test_replace_document(self):
        self._make()
        with Index(self.dir).writer() as writer:
            writer.add_document("d1", "alpha alpha alpha")
            writer.add_document("d1", "beta")
        with Index(self.dir).searcher() as s:
            self.assertEqual(s.search("alpha"), [])
            self.assertEqual([d for d, _ in s.search("beta")], ["d1"])
            self.assertEqual(s.n_docs, 1)

    def test_persistence_across_reopen(self):
        self._make()
        with Index(self.dir).writer() as writer:
            writer.add_documents([("d1", "alpha beta"), ("d2", "beta gamma")])
        with Searcher(self.dir) as s:
            ids = [d for d, _ in s.search("beta")]
            self.assertEqual(sorted(ids), ["d1", "d2"])

    def test_avgdl_updates_incrementally(self):
        self._make()
        with Index(self.dir).writer() as writer:
            writer.add_document("d1", "alpha beta gamma delta")
        with Index(self.dir).searcher() as s:
            self.assertAlmostEqual(s.avgdl, 4.0)
        with Index(self.dir).writer() as writer:
            writer.add_document("d2", "alpha")
        with Index(self.dir).searcher() as s:
            self.assertAlmostEqual(s.avgdl, 2.5)
            self.assertEqual(s.n_docs, 2)
        with Index(self.dir).writer() as writer:
            writer.delete_document("d2")
        with Index(self.dir).searcher() as s:
            self.assertAlmostEqual(s.avgdl, 4.0)
            self.assertEqual(s.n_docs, 1)


class DeterminismTest(IndexTestBase):
    def test_repeat_runs_identical(self):
        batch = self.build_samples()
        with open(QUERIES, encoding="utf-8") as fh:
            queries = [line.rstrip("\n") for line in fh if line.strip()]
        outputs = []
        for _ in range(2):
            with Index(self.dir).searcher() as searcher:
                outputs.append(
                    [tuple(d for d, _ in searcher.search(q)) for q in queries])
        self.assertEqual(outputs[0], outputs[1])


class PerformanceTest(IndexTestBase):
    def test_scale_build_and_query(self):
        n_docs = 10000
        Index.create(self.dir, STOPWORDS)
        words = ["alpha", "beta", "gamma", "delta", "epsilon",
                 "zeta", "eta", "theta", "iota", "kappa"]

        def gen(i):
            base = words[i % len(words):] + words[:i % len(words)]
            rare = "needle%03d" % (i % 200)
            return " ".join(base) + " " + rare + " retrieval system test"

        start = time.time()
        with Index(self.dir).writer() as writer:
            writer.add_documents([("doc-%05d" % i, gen(i))
                                  for i in range(n_docs)])
        build_seconds = time.time() - start

        start = time.time()
        with Index(self.dir).searcher() as s:
            hits = s.search("needle000")
            bool_hits = s.search("needle000 AND NOT needle001")
            phrase_hits = s.search('"retrieval system"')
        query_seconds = time.time() - start

        self.assertGreater(len(hits), 0)
        self.assertGreater(len(bool_hits), 0)
        self.assertEqual(len(phrase_hits), n_docs)
        self.assertLess(build_seconds, 15.0, "建索引过慢：%.2fs" % build_seconds)
        self.assertLess(query_seconds, 3.0, "查询过慢：%.2fs" % query_seconds)


if __name__ == "__main__":
    unittest.main()
