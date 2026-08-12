"""
The single sanctioned way to open a write-capable connection to the database.

Extracted verbatim from market_data/core/connection.py (the source system).
Centralized so journal_mode / busy_timeout / journal_size_limit pragmas are
applied consistently on every write path rather than set ad hoc per call site.

Read-only connections do NOT use this factory: journal_mode is a
whole-database setting a read-only connection cannot (and should not attempt
to) change. See src/db/reader.py, which opens its own mode=ro URI connection.
"""
from __future__ import annotations

import os
import sqlite3

DEFAULT_BUSY_TIMEOUT_MS = 30000
DEFAULT_JOURNAL_SIZE_LIMIT_BYTES = 67108864  # 64 MiB


def connect(
    db_path: str,
    *,
    timeout: float = 30.0,
    check_same_thread: bool = True,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    journal_size_limit_bytes: "int | None" = DEFAULT_JOURNAL_SIZE_LIMIT_BYTES,
) -> sqlite3.Connection:
    """Open a write-capable connection to `db_path` with WAL mode, a busy
    timeout, and a journal_size_limit backstop consistently applied."""
    expanded = os.path.expanduser(db_path)
    db_dir = os.path.dirname(expanded)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(expanded, timeout=timeout, check_same_thread=check_same_thread)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    if journal_size_limit_bytes is not None:
        conn.execute(f"PRAGMA journal_size_limit={int(journal_size_limit_bytes)}")
    return conn
