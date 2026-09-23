"""分词口径（与 README 一致）：

切分 -> 转小写 -> 丢标点 -> 过滤停用词 -> 连续编号。

- 连续的 ASCII 字母或数字算一个词元，转小写（只处理 ASCII）；
- 每个中日韩汉字单独成词元；
- 其余字符（标点、空白、符号、全角字母等）直接丢弃，不占位置；
- 命中停用词的词元丢弃，不占位置；
- 保留下来的词元按出现顺序从 1 开始编号。
"""


def _is_cjk(ch):
    o = ord(ch)
    return (
        0x3400 <= o <= 0x4DBF
        or 0x4E00 <= o <= 0x9FFF
        or 0xF900 <= o <= 0xFAFF
        or 0x20000 <= o <= 0x2A6DF
    )


def _is_ascii_alnum(ch):
    return ch.isascii() and ch.isalnum()


def iter_tokens(text):
    """按切分口径产出词元（未过滤停用词），ASCII 词元已转小写。"""
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if _is_ascii_alnum(ch):
            j = i + 1
            while j < n and _is_ascii_alnum(text[j]):
                j += 1
            yield text[i:j].lower()
            i = j
        elif _is_cjk(ch):
            yield ch
            i += 1
        else:
            i += 1


def tokenize(text, stopwords=frozenset()):
    """完整分词：切分、转小写、丢标点、过滤停用词，返回词元列表。

    返回列表的下标 + 1 即为该词元的位置编号。
    """
    return [tok for tok in iter_tokens(text) if tok not in stopwords]


def load_stopwords(path):
    """加载停用词表：一行一个词，# 开头是注释。"""
    words = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                words.add(line)
    return frozenset(words)
