"""
Resolve the NIFTY 50 index to its Upstox instrument key.

BEHAVIOUR CHANGE FROM THE SOURCE -- read this before trusting it blindly.

The source (brokers/upstox/upstox_instruments.py, UpstoxInstrumentResolver)
downloads Upstox's full NSE contract master (a ~45 MB gzipped JSON file,
https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz) on
every construction, scans it for the NIFTY index row, and falls back to a
hardcoded key only if the scan finds nothing. In practice the hardcoded
fallback is what actually resolves the vast majority of the time -- the
download exists to *reconfirm* a value that rarely changes, at the cost of a
45 MB fetch to construct one object.

This module skips the download and returns the hardcoded key directly:

    NIFTY -> "NSE_INDEX|Nifty 50"

This is a real behaviour change, not a pure extraction. If Upstox ever
changes that key, the source would silently self-correct via its scan; this
module will not -- it will fail the way `resolve_index` below is written to
fail: loudly, naming the key it sent and where to go verify the current one.
That is the deliberate trade this repo makes in exchange for not shipping a
45 MB download as the price of fetching one index's history.
"""
from __future__ import annotations

NSE_CONTRACT_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

UNDERLYING_BY_SYMBOL = {
    "NIFTY": {
        "name": "NIFTY",
        "index_name": "Nifty 50",
        "index_key": "NSE_INDEX|Nifty 50",
    },
}


def resolve_index(symbol: str) -> str:
    """Return the Upstox instrument key for `symbol`'s cash index.

    Only NIFTY is supported -- this repo is NIFTY-spot-only by design (see
    AGENTS.md). Raises LookupError for anything else.
    """
    symbol = symbol.upper()
    meta = UNDERLYING_BY_SYMBOL.get(symbol)
    if not meta:
        raise LookupError(
            f"No hardcoded Upstox instrument key for {symbol!r}. This repo only "
            f"resolves: {', '.join(sorted(UNDERLYING_BY_SYMBOL))}."
        )
    return meta["index_key"]


def describe_key_rejected_error(symbol: str, instrument_key: str, upstream_detail: str) -> str:
    """Build the error message raised when Upstox rejects the hardcoded
    instrument key -- the one failure mode this module cannot self-correct
    for, because it does not download the contract master that would let it.
    """
    return (
        f"Upstox rejected the instrument key {instrument_key!r} for {symbol}. "
        f"This key is a hardcoded constant in src/upstox/instruments.py, not "
        f"looked up dynamically -- Upstox may have changed it. Check the "
        f"current value by downloading and inspecting the NSE contract "
        f"master ({NSE_CONTRACT_MASTER_URL}), filtering for "
        f"segment=NSE_INDEX and name={UNDERLYING_BY_SYMBOL.get(symbol.upper(), {}).get('index_name', symbol)!r}, "
        f"then update UNDERLYING_BY_SYMBOL here. Upstream detail: {upstream_detail}"
    )
