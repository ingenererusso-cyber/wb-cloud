from __future__ import annotations

import base64
import hashlib
import os

from django.conf import settings


def _build_fernet():
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("cryptography is not installed") from exc

    raw_key = os.getenv("API_TOKEN_FERNET_KEY", "").strip()
    if raw_key:
        key_bytes = raw_key.encode("utf-8")
    else:
        # Fallback key derived from SECRET_KEY for backward compatibility.
        # Рекомендуется задать отдельный API_TOKEN_FERNET_KEY в окружении.
        digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        key_bytes = base64.urlsafe_b64encode(digest)

    return Fernet(key_bytes)


def encrypt_secret(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith("enc:"):
        return raw
    fernet = _build_fernet()
    token = fernet.encrypt(raw.encode("utf-8")).decode("utf-8")
    return f"enc:{token}"


def decrypt_secret(value: str | None) -> str:
    stored = (value or "").strip()
    if not stored:
        return ""
    if not stored.startswith("enc:"):
        return stored
    encrypted = stored[4:]
    if not encrypted:
        return ""
    fernet = _build_fernet()
    try:
        return fernet.decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except Exception:
        return ""


def mask_secret(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 10:
        return "*" * len(raw)
    return f"{raw[:6]}...{raw[-4:]}"
