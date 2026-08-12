import os

from src.config import load_dotenv, resolve_db_path


def _write_env(tmp_path, text):
    env_path = tmp_path / ".env"
    env_path.write_text(text)
    return env_path


def test_loads_value_from_file_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("SPOT_DB_PATH", raising=False)
    env_path = _write_env(tmp_path, "SPOT_DB_PATH=/from/dotenv/spot.db\n")

    load_dotenv(env_path)

    assert resolve_db_path() == "/from/dotenv/spot.db"


def test_real_nonempty_env_var_wins_over_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOT_DB_PATH", "/from/real/env/spot.db")
    env_path = _write_env(tmp_path, "SPOT_DB_PATH=/from/dotenv/spot.db\n")

    load_dotenv(env_path)

    assert resolve_db_path() == "/from/real/env/spot.db"


def test_empty_real_env_var_is_treated_as_unset(tmp_path, monkeypatch):
    # export SPOT_DB_PATH= (nothing after the '=') must NOT mask a real
    # value sitting in .env -- see load_dotenv()'s docstring. Before this
    # was fixed, os.environ.setdefault() treated the empty string as
    # "already set" and the .env value never took effect: the "I changed
    # it and nothing happened" trap this repo's own config module exists
    # to prevent, self-inflicted by load_dotenv() itself.
    monkeypatch.setenv("SPOT_DB_PATH", "")
    env_path = _write_env(tmp_path, "SPOT_DB_PATH=/from/dotenv/spot.db\n")

    load_dotenv(env_path)

    assert resolve_db_path() == "/from/dotenv/spot.db"


def test_comments_and_malformed_lines_are_skipped(tmp_path, monkeypatch):
    monkeypatch.delenv("UPSTOX_CLIENT_ID", raising=False)
    env_path = _write_env(
        tmp_path,
        "# a comment\n"
        "\n"
        "NOT_A_VAR_LINE_NO_EQUALS\n"
        "UPSTOX_CLIENT_ID=abc-123\n",
    )

    load_dotenv(env_path)

    assert os.environ.get("UPSTOX_CLIENT_ID") == "abc-123"


def test_matching_quotes_are_stripped(tmp_path, monkeypatch):
    monkeypatch.delenv("UPSTOX_CLIENT_SECRET", raising=False)
    env_path = _write_env(tmp_path, 'UPSTOX_CLIENT_SECRET="quoted-value"\n')

    load_dotenv(env_path)

    assert os.environ.get("UPSTOX_CLIENT_SECRET") == "quoted-value"


def test_missing_file_is_a_silent_no_op(tmp_path, monkeypatch):
    monkeypatch.delenv("SPOT_DB_PATH", raising=False)

    load_dotenv(tmp_path / "does-not-exist" / ".env")

    assert resolve_db_path() is None
