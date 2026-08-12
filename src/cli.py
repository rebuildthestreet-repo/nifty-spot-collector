"""
nifty-spot-collector CLI. Three verbs -- this repo does two things, plus the
one thing needed to get a first-time user able to do them at all.

  nifty-spot auth    [--redirect-uri URL]
  nifty-spot collect --from YYYY-MM-DD --to YYYY-MM-DD [--symbol NIFTY] [--db PATH]
  nifty-spot chart   --from YYYY-MM-DD --to YYYY-MM-DD [--symbol NIFTY] [--db PATH] [--out FILE]

NEW CODE, loosely modelled on app/main.py's shape (the source system,
12 verbs across tokens/EOD/pruning/audits/backfill -- most of which have no
equivalent here). Two things are carried over deliberately, because both
exist in the source to prevent a failure that has actually happened there:

  - `--db` beats guessing. Naming the database path explicitly is preferred
    over deriving it from the working directory or a default, because a
    derived/defaulted path is exactly what let a previous version of this
    system silently create and use an empty database (see src/config.py).
  - The command refuses to run at all if no database path is configured --
    it does not fall through to a default and proceed.

`auth` exists because credentials are this project's single biggest
first-run failure mode: without it, a first-time user has no way to obtain
the token `collect` requires, only instructions telling them to go get one
somehow. It prints the Upstox login URL, waits for a pasted authorization
code (or the whole redirect URL -- see `_extract_auth_code` below), exchanges
it, and saves the resulting access token. It does NOT automate the login
itself -- that is a human clicking through Upstox's own page -- and it does
NOT refresh anything: this repo has no scheduled or background token
handling, on purpose (see src/upstox/session.py's docstring). The daily
ritual is still "run `nifty-spot auth` by hand each morning", just no longer
"and then go figure out how".
"""
from __future__ import annotations

import argparse
import sys
from urllib.parse import parse_qs, urlparse

from src.config import load_dotenv, resolve_db_path, resolve_upstox_token


def _require_db_path(explicit: "str | None") -> str:
    db_path = resolve_db_path(explicit)
    if not db_path:
        print(
            "nifty-spot: no database path configured -- refusing to run.\n"
            "  Pass --db PATH, or set SPOT_DB_PATH.\n"
            "  There is no default path: a fallback here would let this "
            "command silently create and use an empty database and report "
            "zero rows as though that were a real answer.",
            file=sys.stderr,
        )
        sys.exit(2)
    return db_path


def _extract_auth_code(pasted: str) -> str:
    """Accept either a bare authorization code or the full URL Upstox
    redirects to after login (https://.../callback?code=XXXX&state=...).
    A first-time user is at least as likely to copy the whole address bar as
    to isolate the code themselves -- this avoids that being a second way to
    fail before the real API call is even reached."""
    pasted = (pasted or "").strip()
    if pasted.startswith("http://") or pasted.startswith("https://"):
        query = parse_qs(urlparse(pasted).query)
        codes = query.get("code")
        if not codes:
            raise ValueError("That URL has no 'code' query parameter. Paste the code itself, or the full redirect URL.")
        return codes[0]
    return pasted


def _cmd_auth(args: argparse.Namespace) -> None:
    from src.upstox.session import exchange_auth_code, get_login_url, save_token

    try:
        login_url = get_login_url(redirect_uri=args.redirect_uri)
    except ValueError as exc:
        print(f"nifty-spot: {exc}", file=sys.stderr)
        sys.exit(2)

    print("1. Open this URL in a browser and log in to Upstox:\n")
    print(f"   {login_url}\n")
    print("2. After you approve, Upstox redirects to your redirect URI with a ")
    print("   '?code=...' in the address bar. Paste that code, or the whole ")
    print("   address bar contents, below.\n")
    try:
        pasted = input("Paste code or redirect URL: ")
    except EOFError:
        # input() raises this when stdin has no more data to give it -- e.g.
        # run non-interactively, or with stdin redirected from /dev/null or
        # a pipe. `auth` needs a human sitting at a real terminal to open
        # the URL and paste back a code; there is nothing useful this
        # command can do without one. Every other failure path in this CLI
        # prints a formatted "nifty-spot: ..." message instead of letting a
        # traceback surface -- this one didn't, until it was pointed out
        # that a raw EOFError traceback here contradicts every other
        # command's "no stack traces" behaviour.
        print(
            "\nnifty-spot: auth needs an interactive terminal -- no input "
            "was available to read. Run it directly in a terminal you can "
            "type into, not piped, redirected from /dev/null, or run "
            "non-interactively.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        code = _extract_auth_code(pasted)
        response = exchange_auth_code(code, redirect_uri=args.redirect_uri)
        token = response["access_token"]
        save_token(token)
    except (ValueError, KeyError, RuntimeError) as exc:
        # RuntimeError: exchange_auth_code()'s own handling of Upstox
        # rejecting the exchange (HTTPError) or being unreachable
        # (URLError) -- both raised as RuntimeError specifically so this
        # one except clause catches every failure shape from that call,
        # the same "no stack traces" contract every other command keeps.
        print(f"nifty-spot: token exchange failed: {exc}", file=sys.stderr)
        sys.exit(2)

    print("\nToken saved. This token is valid for today only -- Upstox access ")
    print("tokens expire every morning; run `nifty-spot auth` again tomorrow ")
    print("before collecting. Run `./verify.sh` to confirm it works.")


def _cmd_collect(args: argparse.Namespace) -> None:
    from src.collect import collect_range

    db_path = _require_db_path(args.db)
    if not resolve_upstox_token():
        print(
            "nifty-spot: UPSTOX_ACCESS_TOKEN is not set and no credential "
            "file was found. Generate today's token from the Upstox "
            "developer dashboard and set it -- see docs/CREDENTIALS.md.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        results = collect_range(
            db_path=db_path,
            symbol=args.symbol,
            from_date=args.from_date,
            to_date=args.to_date,
            sleep_seconds=args.sleep_seconds,
        )
    except RuntimeError as exc:
        # collect_range raises plain RuntimeError for "no trading days in
        # this range" -- the likeliest first-run mistake there is (a
        # weekend or a holiday week). Every other failure path in this CLI
        # prints a formatted "nifty-spot: ..." message instead of letting a
        # traceback surface; this one didn't, until it was pointed out that
        # a raw traceback here reads as "this tool is broken" to exactly the
        # first-time audience most likely to hit it.
        print(f"nifty-spot: {exc}", file=sys.stderr)
        sys.exit(1)

    total_bars = sum(r["bars"] for r in results)
    print(f"Done: {len(results)} trading day(s), {total_bars} bar(s) total.")


def _cmd_chart(args: argparse.Namespace) -> None:
    from pathlib import Path

    from src.db.reader import SpotBarReader
    from src.render.chart import render_candles

    db_path = _require_db_path(args.db)
    if not Path(db_path).exists():
        # SpotBarReader opens its connection mode=ro, which cannot create a
        # missing file the way a normal connection would -- sqlite3 raises
        # OperationalError("unable to open database file") for that, which
        # is accurate but not helpful. Catch it here instead.
        print(
            f"nifty-spot: no database found at {db_path}. Run `collect` first "
            "to create it.",
            file=sys.stderr,
        )
        sys.exit(1)

    reader = SpotBarReader(db_path)
    try:
        bars = reader.get_spot_bars(args.symbol, args.from_date, args.to_date)
    finally:
        reader.close()

    if not bars:
        print(
            f"nifty-spot: no {args.symbol} bars stored for {args.from_date}..{args.to_date}. "
            "Run `collect` for this range first, or check the range against what's stored "
            "(verify.sh reports row counts).",
            file=sys.stderr,
        )
        sys.exit(1)

    title = args.title or f"{args.symbol} {args.from_date} to {args.to_date}"
    out_path = render_candles(bars, args.out, title)
    print(f"Wrote {out_path} ({len(bars)} bars)")


def main() -> None:
    load_dotenv()  # .env, if present -- real environment variables still win; see src/config.py

    parser = argparse.ArgumentParser(prog="nifty-spot")
    parser.add_argument("--db", metavar="PATH", help="Database path. Overrides SPOT_DB_PATH.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_p = subparsers.add_parser("auth", help="Obtain and save today's Upstox access token")
    auth_p.add_argument("--redirect-uri", dest="redirect_uri", default=None, help="Overrides UPSTOX_REDIRECT_URI")

    collect_p = subparsers.add_parser("collect", help="Fetch and store NIFTY spot 1-minute bars for a date range")
    collect_p.add_argument("--symbol", default="NIFTY", help="Underlying symbol (only NIFTY is supported)")
    collect_p.add_argument("--from", dest="from_date", required=True, metavar="YYYY-MM-DD")
    collect_p.add_argument("--to", dest="to_date", required=True, metavar="YYYY-MM-DD")
    collect_p.add_argument(
        "--sleep-seconds", type=float, default=0.35,
        help="Delay between per-day API calls (default 0.35s, matching the source's only rate-limit defence)",
    )

    chart_p = subparsers.add_parser("chart", help="Render stored bars as a candlestick chart")
    chart_p.add_argument("--symbol", default="NIFTY")
    chart_p.add_argument("--from", dest="from_date", required=True, metavar="YYYY-MM-DD")
    chart_p.add_argument("--to", dest="to_date", required=True, metavar="YYYY-MM-DD")
    chart_p.add_argument("--out", default="chart.svg", help="Output file (.svg or .png; default chart.svg)")
    chart_p.add_argument("--title", default=None, help="Chart title (default: symbol + date range)")

    args = parser.parse_args()
    if args.command == "auth":
        _cmd_auth(args)
    elif args.command == "collect":
        _cmd_collect(args)
    elif args.command == "chart":
        _cmd_chart(args)


if __name__ == "__main__":
    main()
