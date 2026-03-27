from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.config import settings


def _jwt_secret() -> str:
    raw_secret = settings.auth_secret_key
    if len(raw_secret.encode("utf-8")) >= 32:
        return raw_secret
    return hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    real_salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(real_salt), 200000)
    return digest.hex(), real_salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    computed_hash, _ = hash_password(password, salt)
    return hmac.compare_digest(computed_hash, password_hash)


def create_session_token(user: dict[str, Any]) -> str:
    payload = {
        "sub": str(user["id"]),
        "doctor_id": int(user["doctor_id"]),
        "role": user["role"],
        "specialty": user["specialty"],
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_session_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
