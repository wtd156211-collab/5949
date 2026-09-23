"""查询语法解析。

    or_expr  := and_expr (OR and_expr)*
    and_expr := not_expr ((AND)? not_expr)*     # 相邻操作数隐式 AND
    not_expr := NOT not_expr | primary
    primary  := '(' or_expr ')' | '"' 短语 '"' | 词元

优先级 NOT > AND（含隐式）> OR。查询串与文档用同一套分词口径：
标点丢弃、停用词丢弃；只有全大写的 AND/OR/NOT 是运算符。
切完变成空的查询匹配不到任何文档。
"""

from .tokenizer import _is_ascii_alnum, _is_cjk

_OPERATORS = {"AND", "OR", "NOT"}

# 词法单元种类
TERM = "TERM"
PHRASE = "PHRASE"
AND = "AND"
OR = "OR"
NOT = "NOT"
LPAREN = "LPAREN"
RPAREN = "RPAREN"


def _lex(query, stopwords):
    tokens = []
    i = 0
    n = len(query)
    while i < n:
        ch = query[i]
        if ch == '"':
            j = query.find('"', i + 1)
            if j == -1:
                j = n
            phrase = []
            for tok in _lex_operand_text(query[i + 1 : j], stopwords):
                phrase.append(tok)
            tokens.append((PHRASE, phrase))
            i = j + 1
        elif ch == "(":
            tokens.append((LPAREN, None))
            i += 1
        elif ch == ")":
            tokens.append((RPAREN, None))
            i += 1
        elif _is_ascii_alnum(ch):
            j = i + 1
            while j < n and _is_ascii_alnum(query[j]):
                j += 1
            word = query[i:j]
            if word in _OPERATORS:
                tokens.append((word, None))
            else:
                lowered = word.lower()
                if lowered not in stopwords:
                    tokens.append((TERM, lowered))
            i = j
        elif _is_cjk(ch):
            if ch not in stopwords:
                tokens.append((TERM, ch))
            i += 1
        else:
            i += 1
    return tokens


def _lex_operand_text(text, stopwords):
    """短语内部按文档分词口径切词（不识别运算符）。"""
    from .tokenizer import tokenize

    return tokenize(text, stopwords)


class _Node:
    __slots__ = ("kind", "value", "children")

    def __init__(self, kind, value=None, children=None):
        self.kind = kind
        self.value = value
        self.children = children or []

    def __repr__(self):
        if self.value is not None:
            return "%s(%r)" % (self.kind, self.value)
        return "%s(%s)" % (self.kind, ", ".join(map(repr, self.children)))


class _Parser:
    def __init__(self, tokens):
        self._tokens = tokens
        self._pos = 0

    def _peek(self):
        if self._pos < len(self._tokens):
            return self._tokens[self._pos][0]
        return None

    def _next(self):
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def parse(self):
        node = self._parse_or()
        if self._peek() is not None:
            raise ValueError("查询语法错误：意外的 %s" % (self._tokens[self._pos][0],))
        return node

    def _parse_or(self):
        left = self._parse_and()
        while self._peek() == OR:
            self._next()
            right = self._parse_and()
            left = _Node("or", children=[left, right])
        return left

    def _parse_and(self):
        left = self._parse_not()
        while True:
            kind = self._peek()
            if kind == AND:
                self._next()
                right = self._parse_not()
            elif kind in (TERM, PHRASE, NOT, LPAREN):
                right = self._parse_not()  # 隐式 AND
            else:
                break
            left = _Node("and", children=[left, right])
        return left

    def _parse_not(self):
        if self._peek() == NOT:
            self._next()
            return _Node("not", children=[self._parse_not()])
        return self._parse_primary()

    def _parse_primary(self):
        kind = self._peek()
        if kind == LPAREN:
            self._next()
            node = self._parse_or()
            if self._peek() != RPAREN:
                raise ValueError("查询语法错误：缺少右括号")
            self._next()
            return node
        if kind == TERM:
            return _Node("term", value=self._next()[1])
        if kind == PHRASE:
            return _Node("phrase", value=self._next()[1])
        raise ValueError("查询语法错误：此处应为操作数")


def parse(query, stopwords=frozenset()):
    """把查询串解析成语法树。空查询返回 None（匹配不到任何文档）。"""
    tokens = _lex(query, stopwords)
    if not tokens:
        return None
    return _Parser(tokens).parse()
