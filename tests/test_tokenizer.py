import os
import tempfile
import unittest

from kbsearch.tokenizer import Tokenizer, is_cjk_char, load_stopwords, raw_tokens

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "samples")


class RawTokenizeTest(unittest.TestCase):
    def test_ascii_run_and_case(self):
        self.assertEqual(raw_tokens("BM25 doc01"), ["bm25", "doc01"])

    def test_punctuation_dropped_without_positions(self):
        self.assertEqual(raw_tokens("a,b.c!d"), ["a", "b", "c", "d"])

    def test_cjk_each_char(self):
        self.assertEqual(raw_tokens("倒排索引"), ["倒", "排", "索", "引"])

    def test_cjk_and_digits(self):
        self.assertEqual(raw_tokens("BM25排序"), ["bm25", "排", "序"])

    def test_fullwidth_letters_dropped(self):
        self.assertEqual(raw_tokens("ＡＢＣabc"), ["abc"])

    def test_kana_dropped(self):
        self.assertEqual(raw_tokens("検索エンジン"), ["検", "索"])

    def test_cjk_extension(self):
        self.assertTrue(is_cjk_char("\U00020000"))


class StopwordTest(unittest.TestCase):
    def test_filter(self):
        tok = Tokenizer(["the", "of"])
        self.assertEqual(tok.tokenize("State of the Art"), ["state", "art"])

    def test_positions_contiguous(self):
        tok = Tokenizer(["the", "of"])
        tokens = tok.tokenize("state of the art")
        self.assertEqual(tokens, ["state", "art"])

    def test_load_file(self):
        stop = load_stopwords(os.path.join(SAMPLES, "stopwords.txt"))
        self.assertIn("the", stop)
        self.assertIn("的", stop)
        self.assertNotIn("#", stop)

    def test_empty(self):
        tok = Tokenizer(["a"])
        self.assertEqual(tok.tokenize("a a!"), [])


if __name__ == "__main__":
    unittest.main()
