import os
import unittest

from searchlib.query import parse
from searchlib.tokenizer import load_stopwords

STOPWORDS = load_stopwords(
    os.path.join(os.path.dirname(__file__), "..", "samples", "stopwords.txt")
)


def kinds(node):
    if node is None:
        return None
    if node.kind in ("term", "phrase"):
        return (node.kind, node.value)
    return (node.kind, [kinds(c) for c in node.children])


class ParseTest(unittest.TestCase):
    def test_single_term(self):
        self.assertEqual(kinds(parse("bm25")), ("term", "bm25"))

    def test_cjk_operand_expands_to_implicit_and(self):
        self.assertEqual(
            kinds(parse("倒排索引")),
            ("and", [
                ("and", [
                    ("and", [("term", "倒"), ("term", "排")]),
                    ("term", "索"),
                ]),
                ("term", "引"),
            ]),
        )

    def test_implicit_and_between_words(self):
        self.assertEqual(
            kinds(parse("a b")), ("and", [("term", "a"), ("term", "b")])
        )

    def test_precedence_not_and_or(self):
        # a OR b AND NOT c  ->  a OR (b AND (NOT c))
        self.assertEqual(
            kinds(parse("a OR b AND NOT c")),
            ("or", [
                ("term", "a"),
                ("and", [("term", "b"), ("not", [("term", "c")])]),
            ]),
        )

    def test_parentheses(self):
        self.assertEqual(
            kinds(parse("(a OR b) AND c")),
            ("and", [
                ("or", [("term", "a"), ("term", "b")]),
                ("term", "c"),
            ]),
        )

    def test_phrase(self):
        self.assertEqual(
            kinds(parse('"state of the art"', STOPWORDS)),
            ("phrase", ["state", "art"]),
        )

    def test_phrase_with_stopwords_equals_without(self):
        self.assertEqual(
            kinds(parse('"the state of the art"', STOPWORDS)),
            kinds(parse('"state of the art"', STOPWORDS)),
        )

    def test_stopword_only_query_is_empty(self):
        self.assertIsNone(parse("the", STOPWORDS))
        self.assertIsNone(parse("的", STOPWORDS))

    def test_lowercase_and_is_stopword_not_operator(self):
        # 小写 and 是停用词被丢掉，不是运算符；x and y 退化为隐式 AND
        self.assertEqual(
            kinds(parse("x and y", STOPWORDS)),
            ("and", [("term", "x"), ("term", "y")]),
        )

    def test_unclosed_quote_tolerated(self):
        self.assertEqual(kinds(parse('"state art', STOPWORDS)), ("phrase", ["state", "art"]))


if __name__ == "__main__":
    unittest.main()
