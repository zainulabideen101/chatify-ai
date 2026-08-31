"""Symmetric encryption for secure storage of third-party tokens (Shopify / Meta).

Never store raw OAuth tokens. Derives a valid Fernet key from APP_ENCRYPTION_KEY
so any configured secret string works.
"""
import os
import base64
import hashlib
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    raw = os.environ["APP_ENCRYPTION_KEY"].encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    if value is None:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    if token is None:
        return None
    return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
