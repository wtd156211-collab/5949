"""磁盘编码原语：变长整数、文档槽位图、内容寻址文件。"""

import hashlib
import os
import struct

CA_MAGIC = b"CA01"  # term / idmap / docterms 内容寻址文件共用


def encode_vint(value):
    """无符号变长整数，小端 7 位分组，高位表示还有后续字节。"""
    if value < 0:
        raise ValueError("vint 不能为负")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def decode_vint(data, offset):
    result = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("vint 数据被截断")
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, offset
        shift += 7


def write_vint(fh, value):
    fh.write(encode_vint(value))


def append_vint(buf, value):
    """把变长整数直接追加进 bytearray，避免重复函数调用。"""
    while value >= 0x80:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    buf.append(value)


def read_vint(fh):
    result = 0
    shift = 0
    while True:
        chunk = fh.read(1)
        if not chunk:
            raise ValueError("vint 数据被截断")
        byte = chunk[0]
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result
        shift += 7


# ---- 文档槽 -----------------------------------------------------------------

# 状态 0=死槽，1=活槽；length 为该版本分词后的词元数。定长 8 字节。
SLOT_FMT = "<BxxxI"
SLOT_SIZE = struct.calcsize(SLOT_FMT)
assert SLOT_SIZE == 8


def pack_slot(alive, length):
    return struct.pack(SLOT_FMT, 1 if alive else 0, length)


def unpack_slot(data):
    status, length = struct.unpack(SLOT_FMT, data)
    return status == 1, length


# ---- 存活位图 ---------------------------------------------------------------

class Bitmap:
    """按内部文档编号记录存活状态。"""

    def __init__(self, num_bits=0, data=b""):
        self.num_bits = num_bits
        self.bits = bytearray(data)

    @classmethod
    def load(cls, path):
        if os.path.exists(path):
            with open(path, "rb") as fh:
                raw = fh.read()
            num = int.from_bytes(raw[:4], "big")
            return cls(num, raw[4:])
        return cls(0)

    def save(self, path):
        header = self.num_bits.to_bytes(4, "big")
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(header)
            fh.write(self.bits)
        os.replace(tmp, path)

    def _ensure(self, idx):
        need = idx >> 3
        if need >= len(self.bits):
            self.bits.extend(b"\x00" * (need + 1 - len(self.bits)))
        if idx + 1 > self.num_bits:
            self.num_bits = idx + 1

    def set(self, idx, value):
        self._ensure(idx)
        if value:
            self.bits[idx >> 3] |= 1 << (idx & 7)
        else:
            self.bits[idx >> 3] &= ~(1 << (idx & 7))

    def get(self, idx):
        byte_idx = idx >> 3
        if byte_idx >= len(self.bits):
            return False
        return bool(self.bits[byte_idx] & (1 << (idx & 7)))

    def count(self):
        return sum(bin(b).count("1") for b in self.bits)

    def ids(self):
        for byte_idx, byte in enumerate(self.bits):
            if byte:
                for bit in range(8):
                    if byte & (1 << bit):
                        idx = byte_idx * 8 + bit
                        if idx < self.num_bits:
                            yield idx


# ---- 内容寻址文件 (term postings / idmap / docterms) -------------------------

def ca_path(root, content_bytes):
    digest = hashlib.sha1(content_bytes).digest()
    name = digest.hex()
    return os.path.join(root, name[:2], name[2:])


def write_ca(path, payload, header=b""):
    """写 CA 文件：CA_MAGIC + header + payload。已存在且一致则不重写。"""
    body = CA_MAGIC + header + payload
    if os.path.exists(path):
        with open(path, "rb") as fh:
            if fh.read() == body:
                return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(body)
    os.replace(tmp, path)


def remove_ca(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
