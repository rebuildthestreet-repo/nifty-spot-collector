import pytest

from src.upstox.instruments import describe_key_rejected_error, resolve_index


def test_resolve_index_returns_the_hardcoded_nifty_key():
    assert resolve_index("NIFTY") == "NSE_INDEX|Nifty 50"


def test_resolve_index_is_case_insensitive():
    assert resolve_index("nifty") == "NSE_INDEX|Nifty 50"


def test_resolve_index_rejects_unsupported_symbols():
    with pytest.raises(LookupError, match="BANKNIFTY"):
        resolve_index("BANKNIFTY")


def test_key_rejected_error_names_the_key_and_where_to_check_it():
    message = describe_key_rejected_error("NIFTY", "NSE_INDEX|Nifty 50", "404 Not Found")
    assert "NSE_INDEX|Nifty 50" in message
    assert "assets.upstox.com" in message
    assert "404 Not Found" in message
