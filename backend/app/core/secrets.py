from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


DEFAULT_FERNET_KEY = "w4nULr7M_H4dqij2WSgVyhi3F7SxGX2YeliYg5OtTSI="


@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    key = os.getenv("SAVORY_CANVAS_SECRET_KEY", DEFAULT_FERNET_KEY)
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)


def encrypt_text(value: str) -> str:
    return get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_text(value: str) -> str:
    try:
        return get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return value
