"""Nervos Talk 用户 API Key 自助授权流程（Discourse user-api-key 协议）。

无需论坛管理员、无需账号密码：本地生成一次性 RSA 密钥对，引导用户在已登录的
浏览器中打开论坛授权页并点击 Authorize，论坛把用公钥加密的 API Key 通过
auth_redirect 回传本地服务，解密后写入 .env。

纯 Python 实现 RSA PKCS#1 v1.5（加密/解密 + 密钥生成），不引入第三方依赖。
"""
from __future__ import annotations

import base64
import json
import math
import secrets
import time
import urllib.parse

from .config import ROOT

TALK_ORIGIN = "https://talk.nervos.org"
CLIENT_ID = "spark-program-workbench"
APPLICATION_NAME = "Spark Program Workbench"
SCOPES = "write"
_KEY_BITS = 2048
_PENDING_PATH = ROOT / ".talk_auth_pending.json"

_SMALL_PRIMES = (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)


def _miller_rabin(n: int, rounds: int = 16) -> bool:
    if n < 2:
        return False
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _rand_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _miller_rabin(candidate):
            return candidate


def generate_keypair(bits: int = _KEY_BITS) -> dict:
    e = 65537
    while True:
        p = _rand_prime(bits // 2)
        q = _rand_prime(bits // 2)
        if p == q:
            continue
        phi = (p - 1) * (q - 1)
        if math.gcd(e, phi) == 1:
            break
    n = p * q
    d = pow(e, -1, phi)
    return {"n": n, "e": e, "d": d, "bits": bits}


def public_key_pem(key: dict) -> str:
    """生成 Discourse 要求的 PKCS#1 RSA PUBLIC KEY PEM。"""
    n, e = key["n"], key["e"]
    n_bytes = n.to_bytes((n.bit_length() + 7) // 8, "big")
    e_bytes = e.to_bytes((e.bit_length() + 7) // 8, "big")

    def _len_int(data: bytes) -> bytes:
        return b"\x02" + _der_len(len(data)) + data

    def _der_len(length: int) -> bytes:
        if length < 0x80:
            return bytes([length])
        raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
        return bytes([0x80 | len(raw)]) + raw

    seq = _len_int(b"\x00" + n_bytes) + _len_int(e_bytes)
    der = b"\x30" + _der_len(len(seq)) + seq
    body = base64.encodebytes(der).replace(b"\n", b"")
    lines = "\n".join(body[i:i + 64].decode() for i in range(0, len(body), 64))
    return f"-----BEGIN RSA PUBLIC KEY-----\n{lines}\n-----END RSA PUBLIC KEY-----"


def pkcs1v15_encrypt(message: bytes, key: dict) -> bytes:
    n = key["n"]
    k = (n.bit_length() + 7) // 8
    if len(message) > k - 11:
        raise ValueError("消息过长")
    ps_len = k - len(message) - 3
    ps = bytearray()
    while len(ps) < ps_len:
        b = secrets.token_bytes(ps_len - len(ps))
        ps.extend(x for x in b if x != 0)
    block = b"\x00\x02" + bytes(ps[:ps_len]) + b"\x00" + message
    return pow(int.from_bytes(block, "big"), key["e"], n).to_bytes(k, "big")


def pkcs1v15_decrypt(ciphertext: bytes, key: dict) -> bytes:
    n = key["n"]
    k = (n.bit_length() + 7) // 8
    if len(ciphertext) != k:
        raise ValueError("密文长度错误")
    block = pow(int.from_bytes(ciphertext, "big"), key["d"], n).to_bytes(k, "big")
    if not block.startswith(b"\x00\x02"):
        raise ValueError("解密填充头错误")
    sep = block.index(b"\x00", 2)
    return block[sep + 1:]


def _save_pending(state: dict | None) -> None:
    if state is None:
        try:
            _PENDING_PATH.unlink()
        except FileNotFoundError:
            pass
        return
    _PENDING_PATH.write_text(json.dumps(state), encoding="utf-8")


def _load_pending() -> dict | None:
    try:
        return json.loads(_PENDING_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def begin_auth(origin: str = "http://127.0.0.1:8765") -> dict:
    """生成密钥对并保存待授权状态，返回用户需要打开的授权 URL。"""
    key = generate_keypair()
    nonce = secrets.token_urlsafe(16)
    state = {"key": key, "client_id": CLIENT_ID, "nonce": nonce, "created_at": time.time()}
    _save_pending(state)
    query = urllib.parse.urlencode({
        "application_name": APPLICATION_NAME,
        "client_id": CLIENT_ID,
        "scopes": SCOPES,
        "nonce": nonce,
        "public_key": public_key_pem(key),
        "auth_redirect": f"{origin}/api/forum/key-callback",
    }, quote_via=urllib.parse.quote)
    return {"auth_url": f"{TALK_ORIGIN}/user-api-key/new?{query}", "client_id": CLIENT_ID}


def complete_auth(payload_b64: str, expected_username: str | None = None) -> dict:
    """解密授权回传的 payload，返回 {"key": ..., "discourse": ...}。

    expected_username 用于决定写入哪个环境变量名（由调用方处理落盘）。
    """
    pending = _load_pending()
    if not pending:
        raise ValueError("没有待完成的授权，请先在设置页重新发起")
    if time.time() - pending["created_at"] > 1800:
        _save_pending(None)
        raise ValueError("授权已超时（30 分钟），请重新发起")
    try:
        raw = base64.b64decode(payload_b64)
        decrypted = pkcs1v15_decrypt(raw, pending["key"])
        value = json.loads(decrypted)
    except Exception as exc:
        raise ValueError(f"授权回传数据解密失败: {exc}") from exc
    _save_pending(None)
    if not value.get("key"):
        raise ValueError("授权回传数据缺少 key 字段")
    if value.get("nonce") != pending.get("nonce"):
        raise ValueError("授权回传 nonce 不匹配，已作废，请重新发起授权")
    return value
