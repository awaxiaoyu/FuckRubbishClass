"""AES-128-CBC 传输层自检：纯 Python 后备实现必须与 Node 侧真值逐字节一致。

真值来源：work/zhs_hw_client.js 的 aesEnc（浏览器/H5 抓包核对过的密文），
两条向量覆盖「取卷五元组」和「读回 + questionIds」两种明文。
"""

from __future__ import annotations

import base64
import json
import unittest

from app.zhihuishu_exam import EXAM_IV, EXAM_KEY, decrypt_secret, secret_str
from app.zhs_aes import AES


VECTORS = [
    {
        "params": {
            "recruitId": "473252",
            "examId": "ezr92lGe",
            "studentExamId": "5jOVWnR8",
            "schoolId": "2351",
            "courseId": "1000006196",
        },
        "secretStr": (
            "PlZoPn7KYv6tAv9ExzFGdVUqs1/RWsJDzKZBMv4QNgxtKAAZ7+aISmiaNVLFD4WdVe+dhgC"
            "8DACGmOBm2MnQFfOJTl03nLXh0A+HRkixgVcH/P2VOP5Lb3BY7Erq+DTRJjiR28+uH6KR3ur"
            "IZknJTQ=="
        ),
    },
    {
        "params": {
            "recruitId": "473252",
            "examId": "ezr92lGe",
            "stuExamId": "5jOVWnR8",
            "schoolId": "2351",
            "courseId": "1000006196",
            "questionIds": "fqK0AB4/d7NYAqeDTjYenw==",
        },
        "secretStr": (
            "PlZoPn7KYv6tAv9ExzFGdVUqs1/RWsJDzKZBMv4QNgx1V74bptZFKGi7OZoIKURWPFpF4OkV"
            "eB4VcefuP5QalTzAFaUVigbxRDj+CSx5swuGXMl56lD7nnuVpwaXLQdr5zMmRVpub2y24229O"
            "KIYQv5N7aTmUQB+QQqFkPjdIx5eGuulLzjBw0/K830a10wr7lGRS4CJ1qTVFJkXjSfSBQ=="
        ),
    },
]


class ZhihuishuAesTests(unittest.TestCase):
    def test_vectors_match_node_side_ciphertext(self):
        for vector in VECTORS:
            with self.subTest(params=sorted(vector["params"])):
                self.assertEqual(secret_str(vector["params"]), vector["secretStr"])
                self.assertEqual(json.loads(decrypt_secret(vector["secretStr"])), vector["params"])

    def test_pure_python_aes_matches_fips_197(self):
        block = bytes.fromhex("00112233445566778899aabbccddeeff")
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        cipher = AES.new(key, AES.MODE_CBC, b"\x00" * 16)
        self.assertEqual(cipher.encrypt(block).hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")

    def test_key_and_iv_are_the_documented_ones(self):
        self.assertEqual(EXAM_KEY, b"onbfhdyvz8x7otrp")
        self.assertEqual(EXAM_IV, b"1g3qqdh4jvbskb9x")

    def test_base64_payload_is_pkcs7_padded_ciphertext(self):
        payload = base64.b64decode(VECTORS[0]["secretStr"])
        self.assertEqual(len(payload) % 16, 0)
        # 明文按 16 字节补齐后加密，密文块数必须等于 ceil((len(json)+1)/16)
        plaintext = decrypt_secret(VECTORS[0]["secretStr"]).encode("utf-8")
        expected_blocks = (len(plaintext) // 16) + 1
        self.assertEqual(len(payload), expected_blocks * 16)
        self.assertEqual(json.loads(plaintext), VECTORS[0]["params"])


if __name__ == "__main__":
    unittest.main()
