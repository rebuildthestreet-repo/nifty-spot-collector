"""
Shared pytest fixtures.

Mirrors two ideas from the source system's tests/conftest.py, adapted to
this repo's much smaller surface:

  - isolate the trading-day calendar so tests never depend on (or write to)
    whatever SPOT_DB_PATH happens to be set to on the machine running them,
  - fail loudly if a test tries to reach the network, because the only
    network-touching code left in this repo (src/upstox/adapter.py) talks to
    a paid broker API and must never be exercised by the test suite.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_calendar(monkeypatch):
    import src.market.calendar as cal_mod

    monkeypatch.delenv("SPOT_DB_PATH", raising=False)
    cal_mod.clear_trading_day_cache()
    yield
    cal_mod.clear_trading_day_cache()


@pytest.fixture(autouse=True)
def _no_outbound_network(monkeypatch, request):
    """Fail fast on any test that reaches the public internet. A test that
    genuinely needs it marks itself @pytest.mark.network (none currently do)."""
    if request.node.get_closest_marker("network"):
        return

    import urllib.request

    def _blocked(url, *args, **kwargs):
        target = getattr(url, "full_url", url)
        raise AssertionError(
            f"This test tried to reach {target}. This repo's tests must not "
            "use the network -- stub the call or mark the test @pytest.mark.network."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
