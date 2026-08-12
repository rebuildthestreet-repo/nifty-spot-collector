"""
NSE cash-market session boundary for NIFTY spot.

Reduced from market_data/core/market_session.py (the source system), which
tracks separate, independently-dated close times for the cash segment (NIFTY
spot) and the derivatives segment (futures/options) -- SEBI extended the
derivatives close to 15:40 on 2026-08-03 while leaving the cash close at
15:30. This collector only ever fetches spot, so the DERIV segment and the
per-segment plumbing are dropped entirely.

What's kept, and why it still matters with only one segment:

- The rule table is still a *list* of effective-dated rules, not a bare
  constant, even though it currently holds one row. NSE has changed session
  timing before; a future change to the CASH close is exactly the kind of
  thing that should be a new row with an effective_from date, not an edit to
  the existing one -- editing a rule in place would retroactively change what
  this code believes about days already stored in the database.
- The close is EXCLUSIVE. A bar is stamped with its *start* time: the bar
  timestamped 15:29:00 covers 15:29:00-15:29:59, and 15:30:00 is not a valid
  spot bar. Both source brokers have been observed emitting a bar stamped
  exactly at the close (74 such rows reached the source's production
  database before this boundary was corrected there on 2026-07-31), so this
  is a vendor quirk to filter, not a case that cannot arise.
- `session_minute_count` is derived from open/close, never hardcoded as the
  literal 375. The source once hardcoded segment minute-counts as bare
  integers, and a session-length change silently made every date after it
  look "complete" when it was ten bars short -- because nothing recomputed
  the threshold. Deriving it here means a future close-time change is
  reflected everywhere that calls this function, automatically.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date as ddate, datetime, time as dtime, timedelta

from src.market.calendar import is_trading_day


@dataclass(frozen=True)
class SessionRule:
    """The session boundary in force from `effective_from` (inclusive) until
    the next rule's `effective_from`. `close` is EXCLUSIVE."""
    effective_from: str
    open: dtime
    close: dtime


SESSION_RULES: tuple[SessionRule, ...] = (
    SessionRule(effective_from="1900-01-01", open=dtime(9, 15), close=dtime(15, 30)),
)

_RULE_STARTS = [rule.effective_from for rule in SESSION_RULES]


def rule_for_date(date_str: str) -> SessionRule:
    """The SessionRule in force on a calendar date (trading day or not)."""
    index = bisect_right(_RULE_STARTS, str(date_str)[:10]) - 1
    if index < 0:
        raise ValueError(f"No session rule covers {date_str!r}")
    return SESSION_RULES[index]


def session_open(date_str: str) -> dtime:
    return rule_for_date(date_str).open


def session_close(date_str: str) -> dtime:
    """The exclusive close of the cash session on `date_str`."""
    return rule_for_date(date_str).close


def session_minute_count(date_str: str) -> int:
    """Number of one-minute candles a complete spot session holds. 375 today
    -- derived, not hardcoded, so a future close-time change is reflected
    here automatically rather than silently making every day look short."""
    rule = rule_for_date(date_str)
    open_minutes = rule.open.hour * 60 + rule.open.minute
    close_minutes = rule.close.hour * 60 + rule.close.minute
    return max(0, close_minutes - open_minutes)


def _split(timestamp: str) -> "tuple[str, dtime] | None":
    try:
        date_part = timestamp[:10]
        hour, minute, second = (int(part) for part in timestamp[11:19].split(":"))
        return date_part, dtime(hour, minute, second)
    except (TypeError, ValueError):
        return None


def is_market_session_timestamp(timestamp: str) -> bool:
    """True for a regular NSE cash-session candle timestamp.

    The close is EXCLUSIVE: a candle is stamped with its START time, so a
    bar at exactly session close claims to cover a minute the market is
    already shut. Both source brokers have been observed emitting exactly
    that bar; this function is what filters it out.
    """
    parsed = _split(timestamp)
    if parsed is None:
        return False
    date_part, ts_time = parsed
    if not is_trading_day(date_part):
        return False
    rule = rule_for_date(date_part)
    return rule.open <= ts_time < rule.close


def last_bar_start(date_str: str) -> dtime:
    """Start time of the final one-minute candle of the session (15:29 for a
    15:30 close). This is the value to compare an HH:MM candle stamp
    against; `session_close` is the value to compare an instant against."""
    close = session_close(date_str)
    return (datetime.combine(ddate(2000, 1, 1), close) - timedelta(minutes=1)).time()
