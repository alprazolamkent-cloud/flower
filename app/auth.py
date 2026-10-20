"""Хэширование паролей. Пароли в базе не хранятся — только соль и хэш."""

import hashlib
import hmac
import os

ITERATIONS = 200_000


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return salt, digest


def verify_password(password: str, salt: bytes, digest: bytes) -> bool:
    return hmac.compare_digest(hash_password(password, salt)[1], digest)
