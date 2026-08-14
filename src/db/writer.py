"""
Write NIFTY spot bars to the database.

Reduced from the source system's equivalent writer (roughly 700 lines) down
to the spot path. Dropped entirely: futures/options writers,
`session_coverage`, `ingestion_runs`, `finalization_events` and the
BAR_FINALIZED/BAR_CORRECTED event log built on top of them, the
`ingestion_runs` schema-migration block (that table doesn't exist in this
repo's schema at all -- see src/db/schema.py), expiry-calendar and
source-instrument-map bookkeeping.

Kept, and this is the part that matters: `_ensure_instrument` and the
read-compare-write logic in `upsert_spot_bars`. A naive `INSERT OR REPLACE`
would treat every bar in every batch as new, which sounds harmless until you
notice what it costs on a re-run -- re-running a backfill over a day already
in the database would look identical to importing it fresh, and there would
be no way to tell "this batch changed 3 bars" from "this batch touched
nothing". Comparing against what's already stored (within a small float
tolerance, to absorb harmless repr differences) and only writing rows that
actually differ is what makes re-running a backfill over an already-filled
date range a cheap no-op instead of a full rewrite.

Also kept: the source's `_normalize_bars` re-filter, applied here again to
whatever `upsert_spot_bars` is handed. `src/bars.py::normalize_ohlc_rows`
already filters by session before bars reach this class, so on the one path
this repo currently has (Upstox -> src.bars -> here) this second filter never
actually removes anything. It's kept anyway as defense-in-depth for a second
call site that doesn't (a script that hand-builds Bar objects and calls this
class directly, say) -- the source keeps the same redundancy for the same
reason, and it costs one list comprehension per call.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from src.db import connection as db_connection
from src.market.session import is_market_session_timestamp
from src.models import Bar


class SpotBarWriter:
    def __init__(
        self,
        db_path: str,
        source_id: str = "upstox",
        *,
        busy_timeout_ms: int = db_connection.DEFAULT_BUSY_TIMEOUT_MS,
        journal_size_limit_bytes: "int | None" = db_connection.DEFAULT_JOURNAL_SIZE_LIMIT_BYTES,
    ):
        self.db_path = db_path
        # The one create=True in this repo: the collector's own write path,
        # whose first run has no database yet. Every other connection refuses
        # to create -- see src/db/connection.py for why and for the
        # `sqlite-create-ok:` marker convention.
        self.conn = db_connection.connect(
            db_path, create=True, timeout=30.0, check_same_thread=False,
            busy_timeout_ms=busy_timeout_ms,
            journal_size_limit_bytes=journal_size_limit_bytes,
        )
        self.conn.row_factory = sqlite3.Row
        self.source_id = source_id
        self._registered_instruments: set[str] = set()

    def close(self) -> None:
        self.conn.close()

    def _ensure_instrument(self, instrument_id: str, symbol: str, display_name: str) -> None:
        if instrument_id in self._registered_instruments:
            return
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO instruments (
                    instrument_id, symbol, instrument_type, exchange, segment, display_name, first_seen, last_seen
                ) VALUES (?, ?, 'SPOT', 'NSE', 'spot', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                (instrument_id, symbol, display_name),
            )
            self.conn.execute(
                "UPDATE instruments SET last_seen = CURRENT_TIMESTAMP WHERE instrument_id = ?",
                (instrument_id,),
            )
        except sqlite3.Error as exc:
            raise RuntimeError(f"Failed to register instrument '{instrument_id}': {exc}") from exc
        self._registered_instruments.add(instrument_id)

    def upsert_spot_bars(self, symbol: str, run_id: str, bars: list[Bar]) -> dict[str, int]:
        """Insert new bars, update existing ones that changed, skip the rest.
        Returns {"inserted": n, "updated": n, "unchanged": n}."""
        bars = [b for b in bars if is_market_session_timestamp(b.timestamp)]
        if not bars:
            return {"inserted": 0, "updated": 0, "unchanged": 0}

        instrument_id = f"{symbol}:SPOT"
        self._ensure_instrument(instrument_id, symbol, f"{symbol} Spot")

        records = [
            {
                "symbol": symbol,
                "instrument_id": instrument_id,
                "ts": bar.timestamp,
                "trade_date": bar.timestamp[:10],
                "timeframe_sec": 60,
                "source_id": self.source_id,
                "run_id": run_id,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars
        ]

        trade_dates = list({r["trade_date"] for r in records})
        existing: dict[tuple, dict[str, Any]] = {}
        for td in trade_dates:
            cursor = self.conn.execute(
                """SELECT instrument_id, ts, open, high, low, close, volume
                   FROM spot_bars
                   WHERE symbol=? AND trade_date=? AND timeframe_sec=60 AND source_id=?""",
                (symbol, td, self.source_id),
            )
            for row in cursor.fetchall():
                existing[(row["instrument_id"], row["ts"])] = dict(row)

        inserted = updated = unchanged = 0
        with self.conn:
            for r in records:
                key = (r["instrument_id"], r["ts"])
                current = existing.get(key)

                if current is None:
                    self.conn.execute(
                        """INSERT OR REPLACE INTO spot_bars
                            (symbol, instrument_id, ts, trade_date, timeframe_sec, source_id, run_id,
                             open, high, low, close, volume, quality_status, is_native_timeframe)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'raw', 1)""",
                        (
                            r["symbol"], r["instrument_id"], r["ts"], r["trade_date"], r["timeframe_sec"],
                            r["source_id"], r["run_id"], r["open"], r["high"], r["low"], r["close"], r["volume"],
                        ),
                    )
                    inserted += 1
                    continue

                diff = (
                    abs(current["open"] - r["open"]) > 1e-6
                    or abs(current["high"] - r["high"]) > 1e-6
                    or abs(current["low"] - r["low"]) > 1e-6
                    or abs(current["close"] - r["close"]) > 1e-6
                    or abs((current["volume"] or 0.0) - r["volume"]) > 1e-6
                )
                if not diff:
                    unchanged += 1
                    continue

                self.conn.execute(
                    """UPDATE spot_bars SET open=?, high=?, low=?, close=?, volume=?, run_id=?, quality_status='raw'
                       WHERE instrument_id=? AND ts=? AND timeframe_sec=60 AND source_id=?""",
                    (r["open"], r["high"], r["low"], r["close"], r["volume"], r["run_id"],
                     r["instrument_id"], r["ts"], r["source_id"]),
                )
                updated += 1

        return {"inserted": inserted, "updated": updated, "unchanged": unchanged}
