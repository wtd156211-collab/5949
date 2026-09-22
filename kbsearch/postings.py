"""词项倒排表的读写。

单个词项一个内容寻址文件，布局：
    CA_MAGIC | term_utf8
    vint df
    重复 df 次：
        vint doc_delta     内部文档编号相对上一篇的差值（首篇为绝对编号）
        vint tf            该词在文档中的出现次数
        vint pos1          第 1 次出现的位置（从 1 开始）
        vint pos_delta...  后续位置相对上一位置的差值
"""

import os

from .codec import CA_MAGIC, append_vint, decode_vint, encode_vint


def term_path(root, term):
    from .codec import ca_path
    return ca_path(root, term.encode("utf-8"))


def read_posting(path):
    """读整个词项文件，返回 (term, {docid: [positions]})。文件不存在返回 (None, {})。"""
    if not os.path.exists(path):
        return None, {}
    with open(path, "rb") as fh:
        data = fh.read()
    offset = 0
    if data[:4] != CA_MAGIC:
        raise ValueError("倒排文件魔数错误：%s" % path)
    offset = 4
    return _parse(data, offset)


def _parse(data, offset):
    tlen, offset = decode_vint(data, offset)
    term = data[offset:offset + tlen].decode("utf-8")
    offset += tlen
    df, offset = decode_vint(data, offset)
    postings = {}
    doc = 0
    for _ in range(df):
        doc_delta, offset = decode_vint(data, offset)
        doc += doc_delta
        tf, offset = decode_vint(data, offset)
        positions = []
        pos = 0
        for _ in range(tf):
            pdelta, offset = decode_vint(data, offset)
            pos += pdelta
            positions.append(pos)
        postings[doc] = positions
    return term, postings


def write_posting(root, term, postings):
    """postings: {docid: [positions]}；为空则删除文件。返回路径。"""
    path = term_path(root, term)
    if not postings:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return path
    payload = bytearray()
    append_vint(payload, len(postings))
    prev_doc = 0
    for doc in sorted(postings):
        positions = sorted(postings[doc])
        append_vint(payload, doc - prev_doc)
        prev_doc = doc
        append_vint(payload, len(positions))
        prev_pos = 0
        for pos in positions:
            append_vint(payload, pos - prev_pos)
            prev_pos = pos
    from .codec import write_ca
    write_ca(path, bytes(payload), header=encode_vint(len(term.encode("utf-8"))) + term.encode("utf-8"))
    return path


def merge_posting(root, term, adds, deletes):
    """增量更新一个词项：并入 adds，剔除 deletes，重写文件。

    adds: {docid: [positions]}，deletes: set(docid)
    """
    _, current = read_posting(term_path(root, term))
    for doc in deletes:
        current.pop(doc, None)
    for doc, positions in adds.items():
        current[doc] = positions
    write_posting(root, term, current)
