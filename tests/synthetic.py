"""
Generate synthetic OHLC bars for tests. No real market data ships in this
repo, per the extraction rules this repo was built under -- every bar a test
touches is invented here, deterministically, from a seed.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta


def synthetic_upstox_candles(
    date_str: str,
    *,
    start_price: float = 24000.0,
    count: int = 375,
    start_time: str = "09:15:00",
    interval_minutes: int = 1,
    seed: int = 0,
) -> list[list]:
    """Rows shaped like Upstox's raw candle response: [date, open, high, low,
    close, volume, oi]. `date` is an IST-naive "YYYY-MM-DD HH:MM:SS" string,
    matching what src.bars.format_ist_timestamp expects to parse."""
    rng = random.Random(seed)
    start = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M:%S")
    price = start_price
    rows = []
    for i in range(count):
        ts = start + timedelta(minutes=i * interval_minutes)
        open_ = price
        move = rng.uniform(-5.0, 5.0)
        close = round(open_ + move, 2)
        high = round(max(open_, close) + rng.uniform(0, 2.0), 2)
        low = round(min(open_, close) - rng.uniform(0, 2.0), 2)
        rows.append([ts.strftime("%Y-%m-%d %H:%M:%S"), open_, high, low, close, 0, 0])
        price = close
    return rows


def synthetic_bars(date_str: str, **kwargs) -> list:
    """Same generator, returned as src.models.Bar objects with the raw rows
    already run through src.bars.normalize_ohlc_rows."""
    from src.bars import normalize_ohlc_rows

    rows = synthetic_upstox_candles(date_str, **kwargs)
    dict_rows = [
        {"date": r[0], "open": r[1], "high": r[2], "low": r[3], "close": r[4], "volume": r[5]}
        for r in rows
    ]
    return normalize_ohlc_rows(dict_rows, symbol="NIFTY")
