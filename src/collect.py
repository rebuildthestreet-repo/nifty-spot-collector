"""
Collect NIFTY spot 1-minute bars for a date range, one trading day at a time.

NEW CODE -- there is no direct equivalent to copy. The source's orchestrator
(market_data/ingest/broker_seeder.py::seed_day) is built around resolving an
options/futures instrument PLAN for a day (ATM strike, strike window, two
expiries) and only fetches spot as a side effect of computing the ATM strike
for that plan. The spot-only path through it is a small fraction of that
file; this is that fraction, written directly against this repo's own
reduced writer/adapter instead of reusing the plan machinery.

Kept from the source's shape, because both are genuine constraints rather
than stylistic choices:
  - one Upstox API call per trading day (the history API is date-granular --
    see src/upstox/adapter.py's docstring),
  - a small sleep between calls (the only rate-limit defence this codebase
    has ever had, per brokers/ingest/broker_seeder.py),
  - day-by-day writes with no checkpoint file. A range that fails partway
    leaves the earlier days already committed; re-running the same range is
    safe (src/db/writer.py's upsert is idempotent) but re-fetches every day,
    because there is no resume marker. That is a real limitation, not an
    oversight -- see docs/TROUBLESHOOTING.md.
  - the date-prefix filter (`_date_filter` in the source): even though this
    repo always requests `from_date == to_date`, the response is still kept
    only if its own timestamp starts with the date that was actually asked
    for. A same-day request narrows the risk the source's multi-day windows
    carried, but "narrows" isn't "removes" -- nothing here guarantees the API
    never returns a neighbouring day's bar, and a stray one from an adjacent
    trading day would otherwise pass the session filter on its own merits and
    get written under the wrong date.
"""
from __future__ import annotations

import time

from src.db import schema as db_schema
from src.db.writer import SpotBarWriter
from src.market.calendar import get_trading_dates
from src.market.session import session_minute_count
from src.upstox.adapter import UpstoxSpotAdapter
from src.upstox.instruments import resolve_index

DEFAULT_SLEEP_SECONDS = 0.35


def collect_range(
    db_path: str,
    symbol: str,
    from_date: str,
    to_date: str,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
) -> list[dict]:
    """Fetch and store every trading day's spot bars in [from_date, to_date].
    Returns one summary dict per trading day. Raises if the range contains no
    trading day, or (via SpotBarWriter/UpstoxSpotAdapter) on a write or API
    failure -- there is no partial-failure recovery beyond "re-run it"."""
    trading_dates = get_trading_dates(from_date, to_date)
    if not trading_dates:
        raise RuntimeError(
            f"No NSE trading days between {from_date} and {to_date}. "
            "Check the dates and that they aren't both weekends/holidays."
        )

    adapter = UpstoxSpotAdapter()
    instrument_key = resolve_index(symbol)

    writer = SpotBarWriter(db_path, source_id="upstox")
    db_schema.init_schema(writer.conn)

    run_id = f"collect_{symbol}_{from_date}_{to_date}_{int(time.time())}"
    results = []
    try:
        for index, date_str in enumerate(trading_dates):
            bars = adapter.fetch_spot_bars(symbol, instrument_key, date_str, date_str)
            bars = [b for b in bars if b.timestamp.startswith(date_str)]
            expected = session_minute_count(date_str)

            if not bars:
                print(f"[{date_str}] 0 bars returned (expected up to {expected})")
                results.append({"date": date_str, "bars": 0, "inserted": 0, "updated": 0, "unchanged": 0})
            else:
                counts = writer.upsert_spot_bars(symbol, run_id, bars)
                print(
                    f"[{date_str}] {len(bars)}/{expected} bars  "
                    f"inserted={counts['inserted']} updated={counts['updated']} unchanged={counts['unchanged']}"
                )
                results.append({"date": date_str, "bars": len(bars), **counts})

            if sleep_seconds and index < len(trading_dates) - 1:
                time.sleep(sleep_seconds)
    finally:
        writer.close()

    return results
