"""纯 Python AES-128-CBC 后备实现（无 pycryptodome 时使用）。

只做一件小事：给 :mod:`app.zhihuishu_exam` 提供与 ``Crypto.Cipher.AES`` 兼容的
最小接口 —— ``AES.new(key, AES.MODE_CBC, iv).encrypt(data)`` / ``.decrypt(data)``。

为什么需要它：智慧树 exam 网关的 ``secretStr`` 是 AES-128-CBC，而某些运行环境
（例如 Python 3.14 的 MSYS 构建）暂时没有 pycryptodome 的二进制轮子，直接源码
编译会失败。这里用纯 Python 实现保证功能不受影响；有 pycryptodome 时永远不会
走到这里。

正确性由 ``work/zhs_hw_aes_vectors.json`` 的真实 wire 密文逐字节校验。
"""

from __future__ import annotations

MODE_CBC = 2


def _gmul(a: int, b: int) -> int:
    """GF(2^8) 乘法（AES 多项式 x^8 + x^4 + x^3 + x + 1）。"""
    product = 0
    for _ in range(8):
        if b & 1:
            product ^= a
        high = a & 0x80
        a = (a << 1) & 0xFF
        if high:
            a ^= 0x1B
        b >>= 1
    return product


def _rotl8(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


def _build_sbox() -> list[int]:
    """S 盒由「GF(2^8) 乘法逆元 + 仿射变换」现场推导，避免手抄 256 个常量。"""
    inverse = [0] * 256
    for value in range(1, 256):
        for candidate in range(1, 256):
            if _gmul(value, candidate) == 1:
                inverse[value] = candidate
                break
    box = []
    for value in range(256):
        b = inverse[value]
        mixed = b
        for shift in range(1, 5):
            mixed ^= _rotl8(b, shift)
        box.append(mixed ^ 0x63)
    return box


SBOX = _build_sbox()
INV_SBOX = [0] * 256
for _index, _value in enumerate(SBOX):
    INV_SBOX[_value] = _index

RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]
MUL2 = [_gmul(i, 2) for i in range(256)]
MUL3 = [_gmul(i, 3) for i in range(256)]
MUL9 = [_gmul(i, 9) for i in range(256)]
MUL11 = [_gmul(i, 11) for i in range(256)]
MUL13 = [_gmul(i, 13) for i in range(256)]
MUL14 = [_gmul(i, 14) for i in range(256)]


def _expand_key(key: bytes) -> list[list[int]]:
    if len(key) not in (16, 24, 32):
        raise ValueError("AES 密钥长度必须是 16 / 24 / 32 字节")
    size = len(key) // 4
    rounds = size + 6
    words: list[list[int]] = [list(key[4 * i:4 * i + 4]) for i in range(size)]
    for index in range(size, 4 * (rounds + 1)):
        temp = list(words[index - 1])
        if index % size == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[index // size - 1]
        elif size > 6 and index % size == 4:
            temp = [SBOX[b] for b in temp]
        words.append([words[index - size][i] ^ temp[i] for i in range(4)])
    return [sum(words[4 * r:4 * r + 4], []) for r in range(rounds + 1)]


def _add_round_key(state: list[int], round_key: list[int]) -> None:
    for index in range(16):
        state[index] ^= round_key[index]


def _sub_bytes(state: list[int], box: list[int]) -> None:
    for index in range(16):
        state[index] = box[state[index]]


def _shift_rows(state: list[int]) -> None:
    for row in range(1, 4):
        line = [state[row + 4 * col] for col in range(4)]
        line = line[row:] + line[:row]
        for col in range(4):
            state[row + 4 * col] = line[col]


def _inv_shift_rows(state: list[int]) -> None:
    for row in range(1, 4):
        line = [state[row + 4 * col] for col in range(4)]
        line = line[-row:] + line[:-row]
        for col in range(4):
            state[row + 4 * col] = line[col]


def _mix_columns(state: list[int]) -> None:
    for col in range(4):
        a = state[4 * col:4 * col + 4]
        state[4 * col + 0] = MUL2[a[0]] ^ MUL3[a[1]] ^ a[2] ^ a[3]
        state[4 * col + 1] = a[0] ^ MUL2[a[1]] ^ MUL3[a[2]] ^ a[3]
        state[4 * col + 2] = a[0] ^ a[1] ^ MUL2[a[2]] ^ MUL3[a[3]]
        state[4 * col + 3] = MUL3[a[0]] ^ a[1] ^ a[2] ^ MUL2[a[3]]


def _inv_mix_columns(state: list[int]) -> None:
    for col in range(4):
        a = state[4 * col:4 * col + 4]
        state[4 * col + 0] = MUL14[a[0]] ^ MUL11[a[1]] ^ MUL13[a[2]] ^ MUL9[a[3]]
        state[4 * col + 1] = MUL9[a[0]] ^ MUL14[a[1]] ^ MUL11[a[2]] ^ MUL13[a[3]]
        state[4 * col + 2] = MUL13[a[0]] ^ MUL9[a[1]] ^ MUL14[a[2]] ^ MUL11[a[3]]
        state[4 * col + 3] = MUL11[a[0]] ^ MUL13[a[1]] ^ MUL9[a[2]] ^ MUL14[a[3]]


def _encrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    state = list(block)
    _add_round_key(state, round_keys[0])
    for round_index in range(1, len(round_keys) - 1):
        _sub_bytes(state, SBOX)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, round_keys[round_index])
    _sub_bytes(state, SBOX)
    _shift_rows(state)
    _add_round_key(state, round_keys[-1])
    return bytes(state)


def _decrypt_block(block: bytes, round_keys: list[list[int]]) -> bytes:
    state = list(block)
    _add_round_key(state, round_keys[-1])
    for round_index in range(len(round_keys) - 2, 0, -1):
        _inv_shift_rows(state)
        _sub_bytes(state, INV_SBOX)
        _add_round_key(state, round_keys[round_index])
        _inv_mix_columns(state)
    _inv_shift_rows(state)
    _sub_bytes(state, INV_SBOX)
    _add_round_key(state, round_keys[0])
    return bytes(state)


class _CbcCipher:
    def __init__(self, key: bytes, iv: bytes) -> None:
        if len(iv) != 16:
            raise ValueError("AES-CBC 的 IV 必须是 16 字节")
        self._round_keys = _expand_key(key)
        self._iv = bytes(iv)

    def encrypt(self, data: bytes) -> bytes:
        if len(data) % 16:
            raise ValueError("AES-CBC 待加密数据长度必须是 16 的倍数")
        out = bytearray()
        previous = self._iv
        for offset in range(0, len(data), 16):
            chunk = bytes(a ^ b for a, b in zip(data[offset:offset + 16], previous))
            previous = _encrypt_block(chunk, self._round_keys)
            out += previous
        return bytes(out)

    def decrypt(self, data: bytes) -> bytes:
        if len(data) % 16:
            raise ValueError("AES-CBC 待解密数据长度必须是 16 的倍数")
        out = bytearray()
        previous = self._iv
        for offset in range(0, len(data), 16):
            block = data[offset:offset + 16]
            plain = _decrypt_block(block, self._round_keys)
            out += bytes(a ^ b for a, b in zip(plain, previous))
            previous = block
        return bytes(out)


class AES:  # noqa: N801 - 故意与 pycryptodome 同名同形
    """``Crypto.Cipher.AES`` 的最小兼容壳。"""

    MODE_CBC = MODE_CBC

    @staticmethod
    def new(key: bytes, mode: int = MODE_CBC, iv: bytes = b"") -> _CbcCipher:
        if mode != MODE_CBC:
            raise ValueError("纯 Python 后备实现只支持 AES-CBC")
        return _CbcCipher(key, iv)
