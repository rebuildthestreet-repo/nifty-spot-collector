"""
Pins the NSE cash-session boundary this repo relies on everywhere: the
09:15-15:30 window, the close being EXCLUSIVE, and session_minute_count
being derived rather than a hardcoded 375. Adapted from the source's
tests/test_sebi_extended_close_2026_08_03.py, reduced to the CASH-only
shape src/market/session.py keeps (see that file's docstring for why the
DERIV segment and its later close don't apply here).
"""
from src.market.session import (
    is_market_session_timestamp,
    last_bar_start,
    session_close,
    session_minute_count,
    session_open,
)

TRADING_DAY = "2026-06-01"  # Monday
HOLIDAY = "2026-08-15"  # Independence Day
WEEKEND = "2026-06-06"  # Saturday


def test_session_open_and_close():
    assert session_open(TRADING_DAY).strftime("%H:%M:%S") == "09:15:00"
    assert session_close(TRADING_DAY).strftime("%H:%M:%S") == "15:30:00"


def test_last_bar_start_is_one_minute_before_the_close():
    assert last_bar_start(TRADING_DAY).strftime("%H:%M:%S") == "15:29:00"


def test_session_minute_count_is_derived_not_hardcoded():
    # 15:30 - 09:15 = 375 minutes. This must come from open/close, not a
    # literal 375 -- see src/market/session.py's docstring on why the source
    # got burned hardcoding this once.
    assert session_minute_count(TRADING_DAY) == (
        (session_close(TRADING_DAY).hour * 60 + session_close(TRADING_DAY).minute)
        - (session_open(TRADING_DAY).hour * 60 + session_open(TRADING_DAY).minute)
    )
    assert session_minute_count(TRADING_DAY) == 375


def test_session_gate_close_is_exclusive():
    assert is_market_session_timestamp(f"{TRADING_DAY} 15:29:00") is True  # last real candle
    assert is_market_session_timestamp(f"{TRADING_DAY} 15:30:00") is False  # start-stamped: market is shut
    assert is_market_session_timestamp(f"{TRADING_DAY} 15:35:00") is False


def test_session_gate_open_boundary():
    assert is_market_session_timestamp(f"{TRADING_DAY} 09:15:00") is True  # first candle
    assert is_market_session_timestamp(f"{TRADING_DAY} 09:14:00") is False


def test_session_gate_rejects_non_trading_days():
    assert is_market_session_timestamp(f"{HOLIDAY} 10:00:00") is False
    assert is_market_session_timestamp(f"{WEEKEND} 10:00:00") is False


def test_session_gate_rejects_unparseable_timestamps():
    assert is_market_session_timestamp("not-a-timestamp") is False
