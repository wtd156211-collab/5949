import unittest

from kbsearch.parser import QueryError, parse_query

STOP = {"the", "of", "is", "are", "to", "in"}


def t(query):
    return parse_query(query, STOP)


class ParserTest(unittest.TestCase):
    def test_single_word(self):
        self.assertEqual(t("word"), ("TERM", "word"))

    def test_cjk_two_chars_is_implicit_and(self):
        self.assertEqual(t("检索"), ("AND", [("TERM", "检"), ("TERM", "索")]))

    def test_implicit_and_chinese(self):
        self.assertEqual(t("倒排索引"), ("AND", [
            ("TERM", "倒"), ("TERM", "排"), ("TERM", "索"), ("TERM", "引")]))

    def test_explicit_and(self):
        self.assertEqual(t("a AND b"), ("AND", [("TERM", "a"), ("TERM", "b")]))

    def test_or(self):
        self.assertEqual(t("a OR b"), ("OR", [("TERM", "a"), ("TERM", "b")]))

    def test_not_prefix(self):
        self.assertEqual(t("NOT a"), ("NOT", ("TERM", "a")))

    def test_and_not(self):
        self.assertEqual(t("a AND NOT b"),
                         ("AND", [("TERM", "a"), ("NOT", ("TERM", "b"))]))

    def test_parentheses(self):
        self.assertEqual(t("(a OR b) AND c"),
                         ("AND", [("OR", [("TERM", "a"), ("TERM", "b")]),
                                  ("TERM", "c")]))

    def test_phrase(self):
        self.assertEqual(t('"state of the art"'),
                         ("PHRASE", ["state", "art"]))

    def test_phrase_chinese(self):
        self.assertEqual(t('"倒排索引"'),
                         ("PHRASE", ["倒", "排", "索", "引"]))

    def test_phrase_equals_with_leading_stopword(self):
        self.assertEqual(t('"the state of the art"'),
                         t('"state of the art"'))

    def test_precedence_not_and_or(self):
        self.assertEqual(t("NOT a AND b OR c"),
                         ("OR", [
                             ("AND", [("NOT", ("TERM", "a")), ("TERM", "b")]),
                             ("TERM", "c")]))

    def test_implicit_and_beside_or(self):
        self.assertEqual(t("a b OR c"),
                         ("OR", [("AND", [("TERM", "a"), ("TERM", "b")]),
                                 ("TERM", "c")]))

    def test_implicit_not(self):
        self.assertEqual(t("a NOT b"),
                         ("AND", [("TERM", "a"), ("NOT", ("TERM", "b"))]))

    def test_stopword_only_is_empty(self):
        self.assertEqual(t("the"), ("EMPTY", ()))

    def test_empty_query(self):
        self.assertIsNone(t("   "))

    def test_case_insensitive_operators(self):
        self.assertEqual(t("a or b"), t("a OR b"))

    def test_word_containing_and_is_operand(self):
        self.assertEqual(t("android"), ("TERM", "android"))

    def test_unbalanced_paren(self):
        with self.assertRaises(QueryError):
            t("(a AND b")
        with self.assertRaises(QueryError):
            t("a AND b)")

    def test_operator_without_operand(self):
        with self.assertRaises(QueryError):
            t("AND")
        with self.assertRaises(QueryError):
            t("a AND")

    def test_unterminated_quote_is_phrase(self):
        self.assertEqual(t('"state of'), ("PHRASE", ["state"]))

    def test_nested_not(self):
        self.assertEqual(t("NOT NOT a"), ("NOT", ("NOT", ("TERM", "a"))))


if __name__ == "__main__":
    unittest.main()
