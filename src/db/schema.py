"""
Database schema for the NIFTY spot collector.

Reduced from market_data/core/schema.py (the source system), which defines
~20 tables covering spot/futures/options bars, ticks, EOD snapshots and
canonical multi-source views. This collector only ever writes one segment
(spot) from one source (Upstox), so it keeps three tables:

  trading_calendar  -- NSE trading-day / holiday record, read by
                       src/market/calendar.py
  instruments       -- one row per instrument this collector has ever
                       written a bar for (currently always the NIFTY index)
  spot_bars         -- the OHLC bars themselves

`source_id` stays in spot_bars' primary key even though this collector only
ever writes 'upstox'. Dropping it would look harmless with one source and
silently corrupt the table the day a second source is ever ingested under a
different name -- the column is cheap insurance, not unused ceremony.

Deliberately NOT carried over from the source: futures_bars, option_bars,
canonical_source_priority and the *_bars_canonical views. Those exist in the
source to pick one row per candle when two sources cover the same minute.
With exactly one source there is nothing to disambiguate, so the view would
be correct and pure overhead -- see ARCHITECTURE.md for the tradeoff if this
collector ever grows a second source.
"""
import logging
import sqlite3


logger = logging.getLogger(__name__)

SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS trading_calendar (
    exchange            TEXT NOT NULL,
    trade_date          TEXT NOT NULL,
    session_type        TEXT NOT NULL DEFAULT 'regular',
    open_time           TEXT,
    close_time          TEXT,
    notes               TEXT,
    PRIMARY KEY (exchange, trade_date)
)""",
    """CREATE TABLE IF NOT EXISTS instruments (
    instrument_id       TEXT PRIMARY KEY,
    symbol              TEXT NOT NULL,
    instrument_type     TEXT NOT NULL,
    exchange            TEXT NOT NULL DEFAULT 'NSE',
    segment             TEXT,
    expiry_date         TEXT,
    strike              INTEGER,
    option_type         TEXT,
    series_type         TEXT,
    display_name        TEXT NOT NULL,
    first_seen          TEXT,
    last_seen           TEXT,
    metadata_json       TEXT
)""",
    """CREATE TABLE IF NOT EXISTS spot_bars (
    symbol              TEXT NOT NULL,
    instrument_id       TEXT NOT NULL,
    ts                  TEXT NOT NULL,
    trade_date          TEXT NOT NULL,
    timeframe_sec       INTEGER NOT NULL,
    source_id           TEXT NOT NULL,
    run_id              TEXT NOT NULL,
    open                REAL NOT NULL,
    high                REAL NOT NULL,
    low                 REAL NOT NULL,
    close               REAL NOT NULL,
    volume              REAL,
    is_native_timeframe INTEGER NOT NULL DEFAULT 1,
    quality_status      TEXT NOT NULL DEFAULT 'raw',
    quality_notes       TEXT,
    inserted_at         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (instrument_id, ts, timeframe_sec, source_id)
)""",
    """CREATE INDEX IF NOT EXISTS idx_spot_lookup ON spot_bars(symbol, trade_date, timeframe_sec, source_id, ts)""",
    """CREATE INDEX IF NOT EXISTS idx_spot_trade_date ON spot_bars(trade_date)""",
]


def init_schema(conn: sqlite3.Connection):
    """
    Initializes the database schema idempotently.

    Transaction Ownership:
    - If the connection is already in a transaction (conn.in_transaction == True),
      this function will NOT commit or rollback. The caller is responsible for the transaction.
    - If the connection is NOT in a transaction, this function will manage its own
      explicit transaction. It will COMMIT on success or ROLLBACK on failure.
    """
    caller_managed = conn.in_transaction

    if not caller_managed:
        conn.execute("BEGIN")

    try:
        for stmt in SCHEMA_STATEMENTS:
            if stmt.strip():
                conn.execute(stmt)

        # Best-effort, and only this statement: ANALYZE wants a write lock,
        # and failing here is worse than skipping it. The schema is already
        # correct by this point (every statement above is CREATE ... IF NOT
        # EXISTS); stale statistics make reads slower, not wrong.
        try:
            conn.execute("ANALYZE")
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            logger.warning(
                "ANALYZE skipped: %s. Schema is current; query plans may use "
                "stale statistics until the next run.", exc,
            )

        if not caller_managed:
            conn.execute("COMMIT")
    except Exception:
        if not caller_managed:
            conn.execute("ROLLBACK")
        raise
