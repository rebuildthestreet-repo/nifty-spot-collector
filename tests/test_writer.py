import sqlite3

from src.db.schema import init_schema
from src.db.writer import SpotBarWriter
from tests.synthetic import synthetic_bars


def _make_writer(tmp_path):
    db_path = str(tmp_path / "spot.db")
    writer = SpotBarWriter(db_path, source_id="upstox")
    init_schema(writer.conn)
    return writer, db_path


def test_upsert_inserts_new_bars(tmp_path):
    writer, db_path = _make_writer(tmp_path)
    bars = synthetic_bars("2026-06-01", count=10)
    try:
        counts = writer.upsert_spot_bars("NIFTY", "run-1", bars)
    finally:
        writer.close()

    assert counts == {"inserted": 10, "updated": 0, "unchanged": 0}
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM spot_bars").fetchone()[0] == 10
    conn.close()


def test_rerunning_an_unchanged_batch_writes_nothing(tmp_path):
    # This is the property src/db/writer.py's docstring calls out: re-running
    # a backfill over a day already stored must be a no-op, not a rewrite --
    # otherwise there is no way to tell a real correction from a repeat run.
    writer, db_path = _make_writer(tmp_path)
    bars = synthetic_bars("2026-06-01", count=10)
    try:
        writer.upsert_spot_bars("NIFTY", "run-1", bars)
        counts = writer.upsert_spot_bars("NIFTY", "run-2", bars)
    finally:
        writer.close()
    assert counts == {"inserted": 0, "updated": 0, "unchanged": 10}


def test_a_changed_bar_is_updated_not_duplicated(tmp_path):
    writer, db_path = _make_writer(tmp_path)
    bars = synthetic_bars("2026-06-01", count=10)
    try:
        writer.upsert_spot_bars("NIFTY", "run-1", bars)

        revised = list(bars)
        revised[0] = type(revised[0])(
            timestamp=revised[0].timestamp, symbol=revised[0].symbol,
            open=revised[0].open, high=revised[0].high, low=revised[0].low,
            close=revised[0].close + 100.0, volume=revised[0].volume,
        )
        counts = writer.upsert_spot_bars("NIFTY", "run-2", revised)
    finally:
        writer.close()

    assert counts == {"inserted": 0, "updated": 1, "unchanged": 9}
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM spot_bars").fetchone()[0] == 10  # still 10 rows, not 11
    conn.close()


def test_empty_batch_is_a_no_op(tmp_path):
    writer, _ = _make_writer(tmp_path)
    try:
        counts = writer.upsert_spot_bars("NIFTY", "run-1", [])
    finally:
        writer.close()
    assert counts == {"inserted": 0, "updated": 0, "unchanged": 0}
