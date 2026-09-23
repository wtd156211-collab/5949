import os
import unittest

from searchlib.tokenizer import iter_tokens, tokenize, load_stopwords

STOPWORDS = load_stopwords(
    os.path.join(os.path.dirname(__file__), "..", "samples", "stopwords.txt")
)


class TokenizeTest(unittest.TestCase):
    def test_ascii_runs(self):
        self.assertEqual(tokenize("BM25 doc01"), ["bm25", "doc01"])

    def test_ascii_lowercase_only(self):
        self.assertEqual(list(iter_tokens("State ART")), ["state", "art"])

    def test_cjk_single_char_tokens(self):
        self.assertEqual(tokenize("倒排索引"), ["倒", "排", "索", "引"])

    def test_punctuation_dropped(self):
        self.assertEqual(tokenize("a,b.c!d"), ["a", "b", "c", "d"])

    def test_fullwidth_and_symbols_dropped(self):
        self.assertEqual(tokenize("ａｂｃ１２３"), [])
        self.assertEqual(tokenize("a★b"), ["a", "b"])

    def test_mixed(self):
        self.assertEqual(
            tokenize("用BM25重写检索"), ["用", "bm25", "重", "写", "检", "索"]
        )

    def test_stopwords_filtered(self):
        self.assertEqual(tokenize("state of the art", STOPWORDS), ["state", "art"])
        self.assertEqual(tokenize("知识库的检索", STOPWORDS), ["知", "识", "库", "检", "索"])

    def test_positions_skip_stopwords_and_punct(self):
        tokens = tokenize("the state, of the art", STOPWORDS)
        self.assertEqual(tokens, ["state", "art"])
        # 位置从 1 开始连续编号：state=1, art=2
        self.assertEqual(list(enumerate(tokens, 1)), [(1, "state"), (2, "art")])


if __name__ == "__main__":
    unittest.main()
