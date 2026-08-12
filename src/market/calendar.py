"""
NSE trading-day calendar.

Reduced from engine/calendar.py (shared between the source system and a
sibling internal service in the same source codebase), which resolves option/futures expiries in
addition to trading-day status. This collector fetches spot only, so
everything expiry-related is dropped; `is_trading_day` and `get_trading_dates`
are kept close to verbatim, including the caching behaviour.

Trading-day status is looked up in the `trading_calendar` table of THIS
collector's own database first (populated only if you've imported one; the
collector never writes to it itself), falling back to a static weekday +
holiday-list check when the table has no row for the date or the database
isn't reachable yet.

Known gap, carried over from the source rather than silently worked around:
`NSE_HOLIDAYS` below only has entries for 2026, because that is the only
year's holiday list that was available to extract. For a date whose year is
missing from this table and which also has no `trading_calendar` row, the
static fallback treats every weekday as a trading day -- including any
holiday that year. `is_trading_day` logs a warning when this happens instead
of returning a silently wrong answer; it still returns the (possibly wrong)
weekday-only result, because refusing to answer would break every date
range that spans an unlisted year. Add rows to NSE_HOLIDAYS for other years
as you need them, or populate `trading_calendar` from a source you trust.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta
from threading import Lock

from src.config import resolve_db_path

logger = logging.getLogger(__name__)

NSE_HOLIDAYS = {
    2026: {
        "2026-01-26",
        "2026-03-02",
        "2026-03-25",
        "2026-04-02",
        "2026-04-03",
        "2026-04-14",
        "2026-05-01",
        "2026-08-15",
        "2026-10-02",
        "2026-10-26",
        "2026-11-04",
        "2026-11-25",
    },
}

# Per-date cache for is_trading_day(). Trading-day status for a given
# calendar date is immutable once set, so entries never expire.
_TRADING_DAY_CACHE: dict[str, bool] = {}
_TRADING_DAY_CACHE_LOCK = Lock()


def _get_db_connection() -> "sqlite3.Connection | None":
    db_path = resolve_db_path()
    if not db_path or not os.path.exists(db_path):
        # The existence check matters, not just the None check: sqlite3.connect()
        # CREATES a file that doesn't exist yet, and this function's whole
        # contract is "look up trading_calendar if there's something to look
        # it up in". Without this guard, calling is_trading_day() before your
        # first `collect` run -- which is_market_session_timestamp() does on
        # every bar it filters, including verify.sh's own synthetic
        # self-test -- would silently create an empty database file at the
        # configured path. That is precisely the trap this repo's config.py
        # module exists to prevent (see its docstring), reproduced here by
        # a bare sqlite3.connect() call instead. The source this file was
        # extracted from (engine/calendar.py) has the identical bare
        # sqlite3.connect() -- caught here by actually running verify.sh
        # against a not-yet-created database and watching a 0-byte file
        # appear where the diagnostic had just reported "does not exist yet".
        return None
    try:
        return sqlite3.connect(db_path)
    except sqlite3.Error:
        return None


def is_trading_day(date_str: str) -> bool:
    """True if date_str (YYYY-MM-DD) is an NSE trading day.

    Checks this collector's own `trading_calendar` table first; falls back
    to weekday + the static NSE_HOLIDAYS table above when there's no row (or
    no database yet). Result is cached permanently per date after the first
    call.
    """
    if date_str in _TRADING_DAY_CACHE:
        return _TRADING_DAY_CACHE[date_str]

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return False

    with _TRADING_DAY_CACHE_LOCK:
        if date_str in _TRADING_DAY_CACHE:
            return _TRADING_DAY_CACHE[date_str]

        result: bool
        row = None
        conn = _get_db_connection()
        if conn is not None:
            try:
                row = conn.execute(
                    "SELECT session_type FROM trading_calendar WHERE exchange='NSE' AND trade_date=? LIMIT 1",
                    (date_str,),
                ).fetchone()
            except sqlite3.Error:
                row = None
            finally:
                conn.close()

        if row:
            result = row[0] not in ("holiday", "closed")
        else:
            if dt.year not in NSE_HOLIDAYS:
                logger.warning(
                    "is_trading_day(%s): no trading_calendar row and no NSE_HOLIDAYS "
                    "entry for %d -- treating every weekday as a trading day, which "
                    "will be wrong on any actual holiday that year.",
                    date_str, dt.year,
                )
            result = dt.weekday() < 5 and date_str not in NSE_HOLIDAYS.get(dt.year, set())

        _TRADING_DAY_CACHE[date_str] = result
        return result


def clear_trading_day_cache(date_str: "str | None" = None) -> None:
    """Invalidate the is_trading_day cache. No arguments flushes it all."""
    with _TRADING_DAY_CACHE_LOCK:
        if date_str is None:
            _TRADING_DAY_CACHE.clear()
        else:
            _TRADING_DAY_CACHE.pop(date_str, None)


def get_trading_dates(from_date: str, to_date: str) -> list[str]:
    """All NSE trading days in the inclusive range [from_date, to_date]."""
    dt = datetime.strptime(from_date, "%Y-%m-%d")
    end_dt = datetime.strptime(to_date, "%Y-%m-%d")
    result = []
    while dt <= end_dt:
        dt_str = dt.strftime("%Y-%m-%d")
        if is_trading_day(dt_str):
            result.append(dt_str)
        dt += timedelta(days=1)
    return result
