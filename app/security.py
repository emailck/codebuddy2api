import base64
import hashlib
import hmac
import secrets
from typing import Optional

from cryptography.fernet import Fernet

from .config import MASTER_KEY


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left or "", right or "")


def make_key(prefix: str = "sk-cb") -> str:
    return f"{prefix}-{secrets.token_urlsafe(32)}"


def key_prefix(key: str) -> str:
    if len(key) <= 18:
        return key[:8] + "..."
    return f"{key[:12]}...{key[-4:]}"


def _fernet_key() -> bytes:
    digest = hashlib.sha256(MASTER_KEY.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


_FERNET = Fernet(_fernet_key())


def encrypt_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return _FERNET.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return _FERNET.decrypt(value.encode("ascii")).decode("utf-8")
