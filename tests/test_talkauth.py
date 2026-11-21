from __future__ import annotations

import base64
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from spark_web import talkauth


class TalkAuthTests(unittest.TestCase):
    def tearDown(self):
        talkauth._save_pending(None)

    def test_pem_structure_and_roundtrip(self):
        key = talkauth.generate_keypair(1024)
        pem = talkauth.public_key_pem(key)
        self.assertTrue(pem.startswith("-----BEGIN RSA PUBLIC KEY-----"))
        self.assertTrue(pem.endswith("-----END RSA PUBLIC KEY-----"))
        message = json.dumps({"key": "abcdef123"}).encode()
        cipher = talkauth.pkcs1v15_encrypt(message, {"n": key["n"], "e": key["e"]})
        self.assertEqual(talkauth.pkcs1v15_decrypt(cipher, key), message)

    def test_pkcs1v15_interop_with_reference_block(self):
        # 手工构造 PKCS#1 v1.5 加密块验证解密端能正确处理标准格式
        key = talkauth.generate_keypair(1024)
        k = (key["n"].bit_length() + 7) // 8
        message = b"hello talk"
        ps = b"\xab" * (k - len(message) - 3)
        block = b"\x00\x02" + ps + b"\x00" + message
        cipher = pow(int.from_bytes(block, "big"), key["e"], key["n"]).to_bytes(k, "big")
        self.assertEqual(talkauth.pkcs1v15_decrypt(cipher, key), message)

    def test_begin_auth_builds_url_and_pending_state(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(talkauth, "_PENDING_PATH", Path(tmp) / "pending.json"):
            value = talkauth.begin_auth(origin="http://127.0.0.1:8765")
            parsed = urllib.parse.urlparse(value["auth_url"])
            self.assertEqual(f"{parsed.scheme}://{parsed.netloc}", talkauth.TALK_ORIGIN)
            params = urllib.parse.parse_qs(parsed.query)
            self.assertEqual(params["client_id"], [talkauth.CLIENT_ID])
            self.assertEqual(params["scopes"], ["write"])
            self.assertIn("BEGIN RSA PUBLIC KEY", params["public_key"][0])
            self.assertEqual(params["auth_redirect"], ["http://127.0.0.1:8765/api/forum/key-callback"])
            self.assertTrue(talkauth._load_pending())

    def test_complete_auth_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(talkauth, "_PENDING_PATH", Path(tmp) / "pending.json"):
            begin = talkauth.begin_auth()
            pending = talkauth._load_pending()
            payload = {"key": "USER-API-KEY-XYZ", "api": 4, "url": talkauth.TALK_ORIGIN,
                       "nonce": pending["nonce"]}
            cipher = talkauth.pkcs1v15_encrypt(
                json.dumps(payload).encode(), {"n": pending["key"]["n"], "e": pending["key"]["e"]})
            result = talkauth.complete_auth(base64.b64encode(cipher).decode())
            self.assertEqual(result["key"], "USER-API-KEY-XYZ")
            self.assertIsNone(talkauth._load_pending())  # 一次性状态已清除

    def test_complete_auth_without_pending_fails(self):
        with self.assertRaises(ValueError):
            talkauth.complete_auth(base64.b64encode(b"junk").decode())


if __name__ == "__main__":
    unittest.main()
