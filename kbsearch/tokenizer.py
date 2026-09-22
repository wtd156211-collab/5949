"""分词。

口径（见 README，文档和查询共用）：
切分 -> 转小写 -> 丢标点 -> 过滤停用词 -> 连续编号。

- 连续 ASCII 字母/数字是一个词元；
- 每个 CJK 汉字单独成词元；
- 其余字符全部丢弃，不产生词元，也不占位置；
- 词元 ASCII 转小写；
- 命中停用词的词元丢弃，不占位置。
"""

import re

_TOKEN_RE = re.compile(
    "[A-Za-z0-9]+"
    "|[\u3400-\u4dbf\u4e00-\u9fff]"
    "|[\U00020000-\U0002a6df\U0002a700-\U0002ebef]"
)


def is_cjk_char(ch):
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x20000 <= cp <= 0x2A6DF
        or 0x2A700 <= cp <= 0x2EBEF
    )


def raw_tokens(text):
    """切分 + 转小写 + 丢标点。返回未过滤停用词的词元列表。"""
    tokens = []
    for match in _TOKEN_RE.findall(text):
        tokens.append(match.lower() if match[0].isascii() else match)
    return tokens


def load_stopwords(path):
    """读停用词表：一行一个词，# 开头为注释。

    表里的词也走同一套切分口径（停用词本身都是单个词元）。
    """
    stop = set()
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            stop.update(raw_tokens(line))
    return stop


class Tokenizer:
    def __init__(self, stopwords=()):
        self.stopwords = set(stopwords)

    @classmethod
    def from_file(cls, path):
        return cls(load_stopwords(path))

    def tokenize(self, text):
        return [tok for tok in raw_tokens(text) if tok not in self.stopwords]
