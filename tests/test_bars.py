from src.bars import format_ist_timestamp, normalize_ohlc_rows


def test_format_ist_timestamp_strips_tzinfo_after_converting():
    # A UTC timestamp for 04:00 UTC is 09:30 IST -- the result must reflect
    # the conversion, and must carry no tzinfo (see src/bars.py's docstring:
    # everything downstream treats this string as IST by convention only).
    result = format_ist_timestamp("2026-06-01T04:00:00Z")
    assert result == "2026-06-01 09:30:00"


def test_format_ist_timestamp_passes_through_naive_strings():
    assert format_ist_timestamp("2026-06-01 09:15:00") == "2026-06-01 09:15:00"


def test_normalize_ohlc_rows_sorts_and_drops_bars_outside_session():
    # 2026-06-01 is a Monday, an NSE trading day. Rows deliberately out of
    # order, plus one bar exactly at the close (09:16 is fine; 15:30:00 is
    # the exclusive close and must be dropped) and one before the open.
    rows = [
        {"date": "2026-06-01 09:16:00", "open": 2, "high": 2, "low": 2, "close": 2, "volume": 0},
        {"date": "2026-06-01 09:15:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0},
        {"date": "2026-06-01 15:30:00", "open": 9, "high": 9, "low": 9, "close": 9, "volume": 0},  # excluded: close is exclusive
        {"date": "2026-06-01 09:00:00", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0},  # excluded: before open
    ]
    bars = normalize_ohlc_rows(rows, symbol="NIFTY")
    assert [b.timestamp for b in bars] == ["2026-06-01 09:15:00", "2026-06-01 09:16:00"]
    assert bars[0].open == 1
    assert bars[1].open == 2


def test_normalize_ohlc_rows_drops_bars_on_a_non_trading_day():
    # 2026-06-06 is a Saturday.
    rows = [{"date": "2026-06-06 09:15:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]
    assert normalize_ohlc_rows(rows, symbol="NIFTY") == []


def test_normalize_ohlc_rows_carries_volume_through_unchanged():
    # NIFTY spot has no traded volume -- Upstox sends 0 for every bar -- but
    # the field is carried through as-is rather than differenced/dropped, in
    # case a caller ever points this at an instrument that does have one.
    rows = [{"date": "2026-06-01 09:15:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 42.0}]
    bars = normalize_ohlc_rows(rows, symbol="NIFTY")
    assert bars[0].volume == 42.0
