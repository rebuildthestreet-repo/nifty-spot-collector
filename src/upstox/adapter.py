"""
Fetch NIFTY spot 1-minute historical candles from Upstox.

Reduced from brokers/upstox/adapter.py (the source system). Dropped:

- fetch_futures_bars / fetch_option_bars / resolve_instruments and the
  BrokerAdapter ABC they implement -- this repo has one broker and one
  segment, so the abstraction that exists in the source to let ingestion
  code stay broker-agnostic has nothing left to abstract over.
- The expired-instrument API path (`is_expired_instrument_key`,
  `ExpiredInstrumentApi`). That path exists in the source for options and
  futures contracts that have already expired -- a NIFTY spot instrument key
  never matches its trigger condition (it has exactly one '|', the check
  requires two or more), so historical spot data is always fetched from the
  regular history endpoint regardless of how old the date is.
- The intraday-endpoint branch (`get_intra_day_candle_data`, taken when
  `from_date == to_date == today in IST`). This repo is a historical
  backfill tool; per the owner's instruction it does not run unattended or
  against "today" as an implicit default, so the branch that depends on the
  wall clock is dropped rather than carried over unused.

Kept: the 429 retry-with-backoff loop, verbatim in shape. It is Upstox's
entire documented rate-limit defence in the source -- there is no other
throttling anywhere in this codebase's history of this code.
"""
from __future__ import annotations

import time

import upstox_client
from upstox_client.rest import ApiException

from src.bars import normalize_ohlc_rows
from src.models import Bar
from src.upstox.instruments import describe_key_rejected_error
from src.upstox.session import get_upstox_client


class UpstoxSpotAdapter:
    def __init__(self, api_client: "upstox_client.ApiClient | None" = None):
        self.api_client = api_client
        self.history_api: "upstox_client.HistoryV3Api | None" = None

    def authenticate(self) -> None:
        if not self.api_client:
            self.api_client = get_upstox_client()
        self.history_api = upstox_client.HistoryV3Api(self.api_client)

    def fetch_spot_bars(self, symbol: str, instrument_key: str, from_date: str, to_date: str) -> list[Bar]:
        """`from_date`/`to_date` are YYYY-MM-DD. Upstox's history API is
        date-granular -- there is no way to request a sub-day window; any
        finer filtering (e.g. to a specific time range within the day)
        happens client-side in src.bars.normalize_ohlc_rows via the session
        boundary, not in this request."""
        if not self.history_api:
            self.authenticate()
        return self._fetch_with_retry(symbol, instrument_key, from_date, to_date)

    def _fetch_with_retry(self, symbol: str, instrument_key: str, from_date: str, to_date: str, retries: int = 3) -> list[Bar]:
        for attempt in range(retries):
            try:
                response = self.history_api.get_historical_candle_data1(
                    instrument_key=instrument_key,
                    unit="minutes",
                    interval="1",
                    to_date=to_date,
                    from_date=from_date,
                )
                candles = getattr(getattr(response, "data", None), "candles", None) or []
                rows = [
                    {
                        "date": candle[0],
                        "open": candle[1],
                        "high": candle[2],
                        "low": candle[3],
                        "close": candle[4],
                        "volume": candle[5] if len(candle) > 5 else 0,
                    }
                    for candle in candles
                ]
                return normalize_ohlc_rows(rows, symbol=symbol)
            except ApiException as exc:
                if exc.status == 429 and attempt < retries - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                if exc.status in (401, 403):
                    raise RuntimeError(
                        f"Upstox rejected the access token (HTTP {exc.status}). "
                        "The daily token has likely expired -- generate a fresh "
                        "one from the Upstox developer dashboard. See "
                        "docs/CREDENTIALS.md and docs/TROUBLESHOOTING.md."
                    ) from exc
                if exc.status in (400, 404):
                    raise RuntimeError(
                        describe_key_rejected_error(symbol, instrument_key, str(exc))
                    ) from exc
                raise RuntimeError(f"Upstox API error fetching {instrument_key} {from_date}..{to_date}: {exc}") from exc

        raise RuntimeError(f"Max retries exceeded for Upstox API: {instrument_key} {from_date}..{to_date}")
