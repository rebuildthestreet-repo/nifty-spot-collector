"""
The single sanctioned way to open a write-capable connection to the database.

Extracted verbatim from market_data/core/connection.py (the source system).
Centralized so journal_mode / busy_timeout / journal_size_limit pragmas are
applied consistently on every write path rather than set ad hoc per call site.

Read-only connections do NOT use this factory: journal_mode is a
whole-database setting a read-only connection cannot (and should not attempt
to) change. See src/db/reader.py, which opens its own mode=ro URI connection.

CREATION IS OPT-IN, and this is the one thing to understand before editing
this file. `sqlite3.connect()` on a path that does not exist does not fail --
it creates a 0-byte database, and every query afterwards returns zero rows
with no error at all. Not a crash: a confident, empty, wrong answer. This
file previously called it bare, after `os.makedirs()`, so a mistyped
SPOT_DB_PATH created the entire directory tree and an empty database, inside
the module whose whole job is making connections consistent -- and AGENTS.md
invariant 4 has said not to do that since the day this repo was published.

So `connect()` refuses by default and the caller that legitimately creates
says so at the call site. Exactly one path in this repo passes create=True:
the collector's own writer on first run.

THE MARKER CONVENTION. An intentional, reviewable create is marked in the
source with a `# sqlite-create-ok: <reason>` comment on the connect line or
in the comment block immediately above it. The comment is the point -- it
makes the intent visible to a reviewer and greppable by a checker, and it
costs one line, so there is no excuse for the unmarked form. The marker
means "this call is MEANT to create"; never attach it to a connection that
is merely safe, because the next reader will believe it.
"""
from __future__ import annotations

import os
import pathlib
import sqlite3

DEFAULT_BUSY_TIMEOUT_MS = 30000
DEFAULT_JOURNAL_SIZE_LIMIT_BYTES = 67108864  # 64 MiB


def connect(
    db_path: str,
    *,
    create: bool = False,
    timeout: float = 30.0,
    check_same_thread: bool = True,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    journal_size_limit_bytes: "int | None" = DEFAULT_JOURNAL_SIZE_LIMIT_BYTES,
) -> sqlite3.Connection:
    """Open a write-capable connection to `db_path` with WAL mode, a busy
    timeout, and a journal_size_limit backstop consistently applied.

    `create=False` (the default) refuses to bring a database into existence:
    a missing file raises FileNotFoundError naming the path, and the
    connection itself is opened `mode=rw` so SQLite refuses too, whatever
    happens between the check and the open.

    `create=True` is the collector's own first run. It creates the parent
    directory and the database, and the call site must say why.
    """
    expanded = os.path.expanduser(db_path)
    if create:
        db_dir = os.path.dirname(expanded)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        # sqlite-create-ok: the collector's own write path. First run has no
        # database yet, and creating it is the intended behaviour here and
        # nowhere else in this repo. Callers opt in explicitly.
        conn = sqlite3.connect(expanded, timeout=timeout, check_same_thread=check_same_thread)
    else:
        if not os.path.isfile(expanded):
            raise FileNotFoundError(
                "no database at %s. Refusing to connect, because a bare "
                "sqlite3.connect() here would create an empty one and every "
                "query would then return zero rows with no error. If this is "
                "the collector's first run, pass create=True." % expanded
            )
        # Belt and braces: the existence check above gives the useful error
        # message, and mode=rw makes the guarantee SQLite's rather than ours,
        # so a file deleted between the two lines cannot be recreated here.
        # as_uri() does the percent-encoding -- a path containing '?' or '#'
        # silently becomes a different URI otherwise.
        uri = pathlib.Path(os.path.abspath(expanded)).as_uri() + "?mode=rw"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout, check_same_thread=check_same_thread)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    if journal_size_limit_bytes is not None:
        conn.execute(f"PRAGMA journal_size_limit={int(journal_size_limit_bytes)}")
    return conn
