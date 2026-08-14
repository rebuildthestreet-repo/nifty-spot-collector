import sqlite3

from src.market.calendar import clear_trading_day_cache, get_trading_dates, is_trading_day


def test_weekday_in_the_known_holiday_year_is_a_trading_day():
    assert is_trading_day("2026-06-01") is True  # Monday, no DB configured


def test_weekend_is_never_a_trading_day():
    assert is_trading_day("2026-06-06") is False  # Saturday


def test_static_holiday_table_is_honoured_without_a_database():
    assert is_trading_day("2026-08-15") is False  # Independence Day, in NSE_HOLIDAYS


def test_garbage_date_is_not_a_trading_day():
    assert is_trading_day("not-a-date") is False


def test_trading_calendar_table_overrides_the_static_fallback(tmp_path, monkeypatch):
    # A DB row takes priority over both the weekday check and NSE_HOLIDAYS --
    # e.g. an exchange-declared one-off trading Saturday.
    db_path = tmp_path / "spot.db"
    # sqlite-create-ok: this fixture builds the calendar database it is
    # about to test against, in a pytest tmp_path.
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE trading_calendar (exchange TEXT, trade_date TEXT, session_type TEXT)"
    )
    conn.execute("INSERT INTO trading_calendar VALUES ('NSE', '2026-06-06', 'trading')")
    conn.commit()
    conn.close()

    monkeypatch.setenv("SPOT_DB_PATH", str(db_path))
    clear_trading_day_cache("2026-06-06")
    assert is_trading_day("2026-06-06") is True  # Saturday, but the DB says trading
    clear_trading_day_cache("2026-06-06")


def test_get_trading_dates_excludes_weekends_and_holidays():
    # 2026-08-13 Thu, 14 Fri, 15 Sat (also Independence Day), 16 Sun, 17 Mon.
    dates = get_trading_dates("2026-08-13", "2026-08-17")
    assert "2026-08-15" not in dates  # Saturday, and a holiday either way
    assert "2026-08-16" not in dates  # Sunday
    assert dates == ["2026-08-13", "2026-08-14", "2026-08-17"]
