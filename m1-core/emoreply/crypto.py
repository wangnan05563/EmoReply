"""AES-256-GCM 加解密原语（人设库 / RAG 知识库共用）。

与 MTask 端 crypto.ts 同型方案：主密钥 = SHA256(机器指纹 || 可选口令)。
密文格式 `iv.ciphertext`（hex 两段），IV 每次随机。
"""
import hashlib
import os
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

IV_LEN = 12


def derive_key(passphrase: Optional[str] = None) -> bytes:
    """口令缺省时用本机指纹派生（端侧绑定语义）。"""
    seed = passphrase or f"{os.sys.platform}-{os.environ.get('COMPUTERNAME', 'mtask-local')}"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def encrypt(key: bytes, plain: str) -> str:
    iv = os.urandom(IV_LEN)
    ct = AESGCM(key).encrypt(iv, plain.encode("utf-8"), None)
    return iv.hex() + "." + ct.hex()


def decrypt(key: bytes, payload: str) -> str:
    iv_hex, ct_hex = payload.split(".", 1)
    try:
        plain = AESGCM(key).decrypt(bytes.fromhex(iv_hex), bytes.fromhex(ct_hex), None)
    except (InvalidTag, ValueError) as e:
        raise ValueError("解密失败：口令不匹配或数据损坏") from e
    return plain.decode("utf-8")
