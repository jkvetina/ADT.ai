from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

KEY_ENV = "ADT_KEY"


class CryptoError(Exception):
    """Raised when an encrypted ADT secret cannot be processed."""


def resolve_key(explicit: str | None = None) -> str:
    raw = explicit or os.getenv(KEY_ENV)
    if not raw:
        raise CryptoError(f"Encryption key not provided; pass -key or set {KEY_ENV}")
    key_path = Path(raw).expanduser()
    if key_path.is_file():
        value = key_path.read_text(encoding="utf-8").strip()
        if not value:
            raise CryptoError(f"Encryption key file is empty: {key_path}")
        return value
    return raw


def encrypt(value: str, key: str) -> bytes:
    encoded = _fernet(key).encrypt(str(value).encode("utf-8"))
    if decrypt(encoded, key) != value:
        raise CryptoError("Encrypted value failed round-trip verification")
    return encoded


def decrypt(value: bytes | str, key: str) -> str:
    token = value.encode("utf-8") if isinstance(value, str) else value
    try:
        return _fernet(key).decrypt(token).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as error:
        raise CryptoError("Could not decrypt encrypted value") from error


def _fernet(key: str) -> Fernet:
    derivation = PBKDF2HMAC(
        algorithm  = hashes.SHA256(),
        length     = 32,
        salt       = b"",
        iterations = 100000,
    )
    derived = derivation.derive((7 * key).encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived))
