"""
Turn raw Upstox candle rows into sorted, session-filtered Bar objects.

Extracted from brokers/utils.py (the source system). The per-segment
plumbing (`segment` parameter, used in the source to apply a different
session close for spot vs. futures/options) is dropped -- this collector has
only one segment.

Kept verbatim: the ORDER of operations in normalize_ohlc_rows. Rows are
sorted by timestamp *before* volume normalisation runs, because the
cumulative-volume mode differences each row against the previous one in list
order -- normalising first and sorting after would difference rows in
whatever order the API happened to return them, and the `max(0.0, ...)`
clamp in normalize_volume_rows would hide the resulting garbage rather than
erroring on it. Irrelevant for a NIFTY index specifically (an index has no
traded volume; Upstox sends 0 for every bar), but the ordering is kept
because getting it backwards is a silent-corruption bug in the general case,
not a spot-specific one.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from src.market.session import is_market_session_timestamp
from src.models import Bar

IST = ZoneInfo("Asia/Kolkata")


def format_ist_timestamp(value) -> str:
    """Normalise any timestamp the API sends to a naive IST wall-clock
    string "YYYY-MM-DD HH:MM:SS". The result has NO tzinfo -- it is IST by
    convention, not by type. Comparing it against a UTC-aware value without
    converting first is off by 5 hours 30 minutes."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    if dt.tzinfo is not None:
        dt = dt.astimezone(IST).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def normalize_ohlc_rows(rows: Iterable[dict], symbol: str) -> list[Bar]:
    """`rows` are raw Upstox candle dicts (date/open/high/low/close/volume).
    Returns Bar objects, sorted by timestamp, with bars outside the NSE cash
    session silently dropped (both source brokers have been observed
    emitting one bar stamped exactly at session close, which this filters)."""
    normalized = []
    for row in rows:
        timestamp = format_ist_timestamp(row["date"])
        if not is_market_session_timestamp(timestamp):
            continue
        normalized.append(
            {
                "timestamp": timestamp,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0.0),
            }
        )

    normalized.sort(key=lambda row: row["timestamp"])
    return [
        Bar(
            timestamp=row["timestamp"],
            symbol=symbol,
            open=row["open"],
            high=row["high"],
            low=row["low"],
            close=row["close"],
            volume=row["volume"],
        )
        for row in normalized
    ]
