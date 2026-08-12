# Architecture

This describes what the code in `src/` actually does, not what it was meant
to do. If something here and the code disagree, the code is right — file an
issue or fix this doc.

## Module map

```
src/
  cli.py              argparse entry point: auth, collect, chart
  collect.py          orchestrates one collect run, day by day
  config.py           the only place environment variables are read
  models.py           Bar -- the one data shape that crosses module boundaries
  bars.py             raw Upstox rows -> sorted, session-filtered Bar objects

  upstox/
    session.py         token load/save, OAuth login-URL + code-exchange
    instruments.py      NIFTY -> Upstox instrument key (hardcoded, not resolved)
    adapter.py           the one Upstox API call this repo makes, with 429 backoff

  market/
    calendar.py         NSE trading-day calendar (is_trading_day, get_trading_dates)
    session.py           NSE cash-session open/close rules, exclusive-close filter

  db/
    connection.py        WAL-mode write-connection factory
    schema.py             the 3-table schema + init_schema()
    writer.py              SpotBarWriter -- idempotent upsert into spot_bars
    reader.py                SpotBarReader -- read-only query for chart data

  render/
    chart.py                render_candles() -- the ONE function anything
                             outside this file may import from it
```

Dependency direction is strictly downward through that list: `cli.py` calls
`collect.py`/`db.reader`/`render.chart`; `collect.py` calls `db.writer`,
`market.calendar`, `market.session`, `upstox.adapter`, `upstox.instruments`;
`upstox.adapter` calls `bars.py`; `bars.py` calls `market.session`;
`market.session` calls `market.calendar`. Nothing calls back up. `models.py`
and `config.py` are leaves — everything can import them, they import nothing
of this repo's own.

There is no web server, no message queue, no background task, and no code
path that runs without a human invoking a CLI command. Two SQLite
connections exist per run at most (one write connection in `db.writer`, one
read connection in `db.reader` or `market.calendar`'s lookup) and both close
when the command exits.

## The swappable chart interface

```python
render_candles(bars: list[dict], out_path: str | Path, title: str) -> Path
```

defined in `src/render/chart.py`, is the entire contract between this repo
and whatever draws the chart. Every caller (`src/cli.py`'s `chart` command,
`verify.sh`'s self-test) imports only this function; nothing outside
`chart.py` knows or should know that the implementation is matplotlib.

This is deliberate and stated by the person who commissioned this repo:
matplotlib is a **trial-scoped choice**, not a house standard, and the
renderer is expected to be replaced later — with `lightweight-charts`,
Plotly, or a hand-written SVG writer. Replacing it should mean rewriting
this one file and keeping the signature, nothing else in the repo should
need to change.

`bars` is a list of dicts with keys `ts` (str, `"YYYY-MM-DD HH:MM:SS"`),
`open`, `high`, `low`, `close`, `volume` (floats) — exactly what
`SpotBarReader.get_spot_bars()` returns, so the reader and the renderer
agree on shape without either importing the other.

Two behaviours worth knowing if you touch this file:

- **Bars are plotted at equally-spaced integer x-positions, not real
  timestamps.** A real time axis would draw a wide gap for every
  overnight/weekend break between trading sessions. X-axis labels are
  placed at each date boundary found in the data instead.
- **`ax.add_patch()` / `ax.add_line()` do not participate in matplotlib's
  autoscale.** The function explicitly computes and sets `y_min`/`y_max`
  from the bars' own `low`/`high` because of this — omitting that produces
  a chart that renders successfully, writes a real file, and shows nothing
  (matplotlib's default 0–1 axes). This was a real bug caught during this
  repo's own build; see `docs/TROUBLESHOOTING.md`.

`render_candles()` raises `ValueError` on an empty `bars` list rather than
drawing a technically-valid, meaningless blank chart.

## Three-stage filtering

A bar can be dropped or admitted at three independent points between the
Upstox API response and a row in `spot_bars`. All three exist because the
system this repo was extracted from needed all three, and each catches a
different failure:

1. **`src/bars.py::normalize_ohlc_rows`** filters by
   `is_market_session_timestamp()` (the exclusive-close rule) as rows are
   converted from raw Upstox dicts to `Bar` objects. This is where the
   09:15–15:29 window is actually enforced, and where a bar stamped exactly
   at 15:30:00 — which both source brokers have been observed sending — gets
   dropped.
2. **`src/db/writer.py::SpotBarWriter.upsert_spot_bars`** re-applies the same
   session filter to whatever it's handed, before writing. On the one path
   this repo currently has (Upstox → `bars.py` → here), this second filter
   never actually removes anything — it's defense-in-depth for a second call
   site that doesn't already filter (a script that hand-builds `Bar` objects
   and calls `SpotBarWriter` directly, say).
3. **`src/collect.py::collect_range`** additionally keeps only bars whose own
   timestamp starts with the trading date that was actually requested,
   before handing them to the writer. Because `collect_range` always
   requests `from_date == to_date`, this narrows a risk rather than
   eliminating a live one — but "narrows" isn't "removes": nothing
   guarantees the Upstox API never returns a neighbouring day's bar, and a
   stray one would otherwise pass stage 1's session filter on its own merits
   (a real timestamp in a real session) and get written under the wrong
   trade date.

Don't remove one of these three assuming another already covers it — they
were separated on purpose, and #1 and #2 currently overlap completely by
design, not by accident.

## The idempotent upsert

`SpotBarWriter.upsert_spot_bars` is not `INSERT OR REPLACE`. For each
incoming bar it looks up the existing row (if any) by
`(instrument_id, ts, timeframe_sec, source_id)`, compares OHLCV with a
`1e-6` absolute float tolerance, and only performs a write — insert or
update — when something actually differs. Bars that match what's already
stored are counted as `unchanged` and touch the database not at all.

This is what makes re-running `collect` over a date range you've already
collected a cheap no-op instead of a full rewrite: run it twice over the
same range with the same data and the second run reports
`{"inserted": 0, "updated": 0, "unchanged": N}`. A naive replace-everything
upsert would report every bar as freshly written on every run, and there
would be no way to tell "this run actually changed 3 bars" from "this run
touched nothing".

## Session and calendar rules

`src/market/session.py::SESSION_RULES` is a list of effective-dated rules
(`effective_from`, `open`, `close`), not a bare constant — currently one
entry, `09:15`–`15:30` from `1900-01-01`. The close is **exclusive**: a bar
stamped with its start time at 15:29:00 covers the minute 15:29:00–15:29:59,
and 15:30:00 is already outside the session. `session_minute_count(date)` is
derived from `open`/`close` (currently 375), never hardcoded — if a future
session-timing change ever lands as a new rule row, every caller that asks
"how many bars should a full day have" reflects it automatically, rather
than silently reporting every subsequent day as short.

`src/market/calendar.py::is_trading_day(date)` checks this collector's own
`trading_calendar` table first (populated only if you've imported one — this
repo's own code never writes to it), falling back to a static weekday +
`NSE_HOLIDAYS` check when there's no row for the date or the database isn't
reachable yet. `NSE_HOLIDAYS` only has entries for 2026; a date in another
year with no `trading_calendar` row logs a warning and falls back to
weekday-only, which will be wrong on any real holiday that year.
`is_trading_day` results are cached per-date for the life of the process —
trading-day status for a given calendar date is immutable once known.

## `.env` loading and precedence

`src/config.py::load_dotenv()` reads `REPO_DIR/.env` (if present) into
`os.environ`, called once at the top of `src/cli.py::main()` and once near
the top of `verify.sh`'s own Python check script -- both before anything
else reads an environment variable. **A real environment variable always
wins over a value from `.env`**, provided it's non-empty -- `load_dotenv()`
only fills in a variable that is either absent from `os.environ` or present
but empty (`export SPOT_DB_PATH=` with nothing after the `=`), never
overwriting a real, non-empty value. That last part matters: without it, an
empty export would silently mask a perfectly good value sitting in `.env` --
"I changed it and nothing happened," the same failure class `resolve_db_path()`
exists to prevent, just self-inflicted this time. This makes the three ways
a variable can arrive (a value already exported in your shell profile,
`docker-compose.yml`'s `env_file:` setting it inside the container, or this
file) consistent with each other instead of order-dependent.

Before this existed, a native install required a manual `set -a; source .env; set +a`
between editing `.env` and running any command — independent testing
identified that extra, easy-to-forget step as the most likely point a
first-time, non-developer user gave up. `docker-compose.yml`'s `env_file:`
already made Docker immune to this; `load_dotenv()` brings the native install to the
same place. It is not a general-purpose dotenv library — no interpolation,
no multi-line values, no export syntax — just `KEY=VALUE` lines, `#`
comments, and optional matching quotes around the value, which is all five
of this repo's variables have ever needed.

## `verify.sh`'s exit-code contract

`verify.sh` prints one `[OK]`/`[FAIL]`/`[SKIP]` line per check and ends with
a pasteable diagnostic block, but scripting against it should use the exit
code, not parse the text:

| Exit | Meaning |
|---|---|
| `0` | Fully green — everything checked is present and working. |
| `1` | Something is broken — at least one check genuinely `FAIL`ed. |
| `2` | `verify.sh` itself could not even start (no `python3` found, or `src/` isn't importable). |
| `3` | Configured correctly, but no Upstox token is set — one `auth` run away from fully working. |

**Exit 3 is not an error.** The access token expires every morning (see the
README's first "thing that will bite you"), so "configured correctly, no
token yet" is a state every user lands in daily, by design — on a fresh
clone before the first `auth`, and again every morning after the previous
token expires. Folding it into exit 0 would hide a real distinction ("fully
usable" vs. "one manual step from usable") from anything checking only
`$?`; folding it into exit 1 would make a normal, expected, daily state
look like a bug. It gets its own code instead.

This has one consequence worth knowing if you touch `docker-compose.yml`:
`docker compose up` runs `verify.sh` as the container's command, and a
nonzero container exit reads as a failed build to Compose and to anyone
glancing at the result — which every fresh build's first run would be,
since there's no token yet at that point. `docker-compose.yml`'s `command:`
wraps `verify.sh` in a small shell conditional that maps exit 3 to a
successful (`0`) container exit specifically, while still letting exit 1
and exit 2 fail the build as they should. See that file's comments for the
exact wrapper.

## Known limits, stated plainly

- **No resume on a failed multi-day `collect`.** A range that fails partway
  leaves the earlier days already committed (the upsert is idempotent, so
  this is safe) but there is no checkpoint file — re-running re-fetches
  every day in the range from Upstox, not just the ones that failed.
- **Upstox's historical retention depth is unverified.** Nothing in this
  repo has confirmed how far back 1-minute spot data is actually available.
- **Upstox's rate limit is undocumented.** The only defence is a 3-attempt
  backoff on HTTP 429 plus a 0.35-second delay between per-day requests
  (`--sleep-seconds` on `collect`). No confirmed quota exists to code
  against.
- **`volume` is always `0`.** NIFTY spot is an index print with no traded
  volume; the column is kept for schema fidelity, not because it carries
  information for this instrument.
- **Docker is confirmed on macOS, unverified on Windows and Linux.** See
  the README's prerequisites section — `[UNVERIFIED: docker]` (where it
  still appears) marks the remaining claims about Docker not yet confirmed
  against a real daemon on those platforms.
- **Tested and shipped are currently different Python runtimes.** The
  pinned dependencies in `requirements.txt` target **Python 3.12** — that's
  what `Dockerfile` installs (`python:3.12.13-slim-bookworm`), and what the
  `upstox-python-sdk`/`urllib3`/`certifi`/etc. pins were taken from (the
  source system's own 3.12.13 venv; see that file's header comment). 3.12
  is authoritative for what this repo is built and shipped against.
  Separately, `./setup.sh` and the test suite have been **run and passed**
  under Python 3.14.4 (the system `python3` on the machine both the initial
  build and independent testing happened on) — dependencies installed
  cleanly with no compiler needed and no version conflicts, but 3.14 is an
  *observed-working* runtime, not the target one. If a future dependency
  bump ever breaks on 3.12 while passing on 3.14 (or vice versa), 3.12 is
  the one to fix for.
