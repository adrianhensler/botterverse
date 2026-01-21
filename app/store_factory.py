from __future__ import annotations

import os

from .store import InMemoryStore
from .store_sqlite import SQLiteStore


def build_store() -> InMemoryStore | SQLiteStore:
    store_type = os.getenv("BOTTERVERSE_STORE", "memory").lower()
    if store_type == "sqlite":
        sqlite_path = os.getenv("BOTTERVERSE_SQLITE_PATH", "data/botterverse.db")
        return SQLiteStore(sqlite_path)
    return InMemoryStore()
