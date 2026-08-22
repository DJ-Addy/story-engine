"""Password hashing (PBKDF2-HMAC-SHA256 + per-user salt) and JWT tokens."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

import jwt

_ALGORITHM = "HS256"
_PBKDF2_ITERATIONS = 200_000
ACCESS_TOKEN_TTL_SECONDS = 60 * 60  # 60 minutes per PRD


def _secret() -> str:
    return os.environ.get("STORY_ENGINE_SECRET", "dev-secret-change-me")


def new_salt() -> str:
    return secrets.token_hex(16)


def hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return digest.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt), expected_hash)


def create_token(user_id: str) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "iat": now, "exp": now + ACCESS_TOKEN_TTL_SECONDS}
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


def decode_token(token: str) -> str | None:
    """Return the user id from a valid token, or None if invalid/expired."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None
