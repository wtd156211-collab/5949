"""查询解析。

语法：
    expr     := or_expr
    or_expr  := and_expr ("OR" and_expr)*
    and_expr := not_expr (("AND" | <隐式>) not_expr)*
    not_expr := "NOT" not_expr | atom
    atom     := "(" expr ")" | 短语 | 文本块

词法层面，双引号外：
- 空白不作为词元；
- 括号各自独立；
- 其余连续非分隔字符是一个"文本块"，整块经同一分词口径切词；
  整块小写后恰好等于 and/or/not 时按布尔运算符处理。
双引号内的内容整体作为短语，按同一口径切词。
"""

from .tokenizer import raw_tokens

# 注意：and/or/not 在停用词表里，但作为引号外的独立运算符保留其语法含义。
_OPERATORS = {"and", "or", "not"}


class QueryError(ValueError):
    pass


class Lexer:
    def __init__(self, text):
        self.text = text
        self.tokens = []
        self._lex()

    def _lex(self):
        text = self.text
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch.isspace():
                i += 1
            elif ch == "(":
                self.tokens.append(("LPAREN", "("))
                i += 1
            elif ch == ")":
                self.tokens.append(("RPAREN", ")"))
                i += 1
            elif ch == '"':
                j = text.find('"', i + 1)
                if j == -1:
                    phrase = text[i + 1:]
                    i = n
                else:
                    phrase = text[i + 1:j]
                    i = j + 1
                self.tokens.append(("PHRASE", phrase))
            else:
                j = i
                while j < n:
                    c = text[j]
                    if c.isspace() or c in '()"':
                        break
                    j += 1
                self.tokens.append(("ATOM", text[i:j]))
                i = j


class Parser:
    def __init__(self, lex_tokens, stopwords):
        self.tokens = lex_tokens
        self.pos = 0
        self.stopwords = set(stopwords)

    def _peek(self):
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def _next(self):
        tok = self._peek()
        if tok is not None:
            self.pos += 1
        return tok

    def _cut(self, text):
        return [t for t in raw_tokens(text) if t not in self.stopwords]

    def parse(self):
        if not self.tokens:
            return None
        node = self._parse_or()
        if self._peek() is not None:
            raise QueryError("括号不匹配：右括号过多")
        return node

    def _parse_or(self):
        children = [self._parse_and()]
        while True:
            tok = self._peek()
            if tok and tok[0] == "ATOM" and tok[1].lower() == "or":
                self._next()
                children.append(self._parse_and())
            else:
                break
        return ("OR", children) if len(children) > 1 else children[0]

    def _parse_and(self):
        children = [self._parse_not()]
        while True:
            tok = self._peek()
            if tok is None or tok[0] == "RPAREN":
                break
            if tok[0] == "ATOM" and tok[1].lower() == "or":
                break
            if tok[0] == "ATOM" and tok[1].lower() == "and":
                self._next()
                children.append(self._parse_not())
                continue
            # 显式 NOT 由 _parse_not 自己消费；这里只处理相邻操作数的隐式 AND。
            children.append(self._parse_not())
        return ("AND", children) if len(children) > 1 else children[0]

    def _parse_not(self):
        tok = self._peek()
        if tok and tok[0] == "ATOM" and tok[1].lower() == "not":
            self._next()
            return ("NOT", self._parse_not())
        return self._parse_atom()

    def _parse_atom(self):
        tok = self._next()
        if tok is None:
            raise QueryError("查询不完整：缺少操作数")
        kind, value = tok
        if kind == "LPAREN":
            node = self._parse_or()
            close = self._next()
            if close is None or close[0] != "RPAREN":
                raise QueryError("括号不匹配：缺少右括号")
            return node
        if kind == "RPAREN":
            raise QueryError("括号不匹配：意外的右括号")
        if kind == "PHRASE":
            return ("PHRASE", self._cut(value))
        if value.lower() in _OPERATORS:
            raise QueryError("运算符 %r 缺少操作数" % value)
        words = self._cut(value)
        if len(words) == 1:
            return ("TERM", words[0])
        if not words:
            return ("EMPTY", ())
        return ("AND", [("TERM", w) for w in words])


def parse_query(text, stopwords=()):
    """把查询串解析成 AST。空查询返回 None。"""
    lexer = Lexer(text)
    parser = Parser(lexer.tokens, stopwords)
    return parser.parse()
