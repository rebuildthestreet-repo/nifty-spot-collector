"""
Read stored spot bars back out, for the chart command.

Reduced from the source system's equivalent reader (roughly 700 lines) down to
one query. Dropped: canonical-source selection (`get_canonical_source` picks
between multiple sources disagreeing on the same candle -- irrelevant with
exactly one source), completeness thresholds, session coverage, quality
events, futures/options reads.

Kept: the read-only `file:...?mode=ro` connection URI (a genuine read-only
open, not just "an app that promises not to write" -- SQLite enforces it),
and the `ORDER BY ts ASC`. The ORDER BY is not decoration: SQLite returns
rows in whatever order the index underneath happens to hold them, which for
this schema is not chronological order.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional


class SpotBarReader:
    def __init__(self, db_path: str):
        if not db_path:
            raise ValueError("SpotBarReader requires an explicit db_path.")
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            uri = f"file:{self.db_path}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout=30000")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def get_spot_bars(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
        source_id: str = "upstox",
    ) -> list[dict[str, Any]]:
        """All spot bars for `symbol` with trade_date in [from_date, to_date],
        ordered chronologically."""
        rows = self.conn.execute(
            """SELECT ts, open, high, low, close, volume
               FROM spot_bars
               WHERE symbol=? AND trade_date>=? AND trade_date<=? AND timeframe_sec=60 AND source_id=?
               ORDER BY ts ASC""",
            (symbol, from_date, to_date, source_id),
        ).fetchall()
        return [dict(r) for r in rows]
