# AGENTS.md

For a coding agent extending this repo. Assume no context beyond what's in
this file and `docs/ARCHITECTURE.md` — read that one first, this one is the
rules layered on top of it.

## What this repo is, in one sentence

Collects NIFTY 50 spot 1-minute OHLC bars from Upstox for a date range and
renders them as a candlestick chart. Two things, nothing else.

## What this repo is not, and must not become

- **Not a daemon.** No scheduler, no background process, no long-running
  service. Every code path runs once, does its job, and exits. If a task
  asks you to add scheduling, a watch mode, or "run this every morning
  automatically" — stop and confirm with whoever's asking. It contradicts
  the design, not just the current feature set.
- **Not multi-instrument, multi-segment, or multi-broker** unless someone
  explicitly asks for that expansion. It collects NIFTY spot only, from
  Upstox only. See "Where to add things" below for exactly which
  simplifications are spot-only shortcuts that a futures/options extension
  would need to undo.

## Invariants — do not change these without a very good reason, and say so explicitly if you do

1. **No automated token refresh, ever.** `src/upstox/session.py` has
   `exchange_auth_code()`, which makes a real HTTP call to Upstox — but only
   when a human runs `nifty-spot auth` and pastes a code back by hand. There
   is no cron job, no retry-on-expiry, no "refresh token" flow. This is a
   deliberate safety boundary stated explicitly by the project owner, not an
   oversight. If you're tempted to add convenience here, don't — ask first.

2. **No config file format.** Every setting is an environment variable, read
   in exactly one place (`src/config.py`, plus `os.environ.get()` directly
   in `src/upstox/session.py::load_credentials()` for the three app-registration
   values). Don't introduce a `settings.yml`, a `.ini`, or any other config
   format — `.env` + `.env.example` is the whole system.

3. **No default paths that get silently created.** `resolve_db_path()` and
   `resolve_upstox_token()` return `None` when unset — they do not fall
   back to a guessed path. This is not a style preference: the system this
   repo was extracted from was bitten twice by exactly that fallback (an
   unconfigured run silently created an empty database at a `$HOME`-relative
   default and reported "0 rows" looking exactly like success). Every
   caller of these functions must treat `None` as "refuse to proceed", not
   "use something reasonable instead".

4. **`sqlite3.connect()` is not a read operation.** It creates the file if
   it doesn't exist. Any code that means "look this up if the database
   happens to exist" must check `os.path.exists(db_path)` (or use a
   `mode=ro` URI connection, like `SpotBarReader` does) before connecting.
   `src/market/calendar.py::_get_db_connection` had exactly this bug,
   inherited from the source system, found by actually running `verify.sh`
   and watching a file appear where the diagnostic had just said "does not
   exist yet". Don't reintroduce it in a new module.

   **This invariant was written on the day this repo was published and the
   connection factory broke it anyway.** `src/db/connection.py` called
   `os.makedirs()` and then connected bare, so a mistyped `SPOT_DB_PATH`
   created a whole directory tree and an empty database — in the one file
   whose entire job is making connections consistent, and in the one file a
   reader would trust without checking. It was carried verbatim from the
   source system, by an extraction that documented the bug in this very
   list. **Three ways of stating a rule are not three enforcements of it.**

   The shape it now has, and the shape to copy:

   - **Creation is opt-in.** `connection.connect()` takes `create=False` by
     default; a missing file raises `FileNotFoundError` naming the path, and
     the connection is opened `mode=rw` so SQLite refuses independently of
     our check.
   - **Exactly one call site passes `create=True`** — `SpotBarWriter`, the
     collector's own write path, whose first run legitimately has no
     database. It says so at the call site.
   - **The marker convention.** A deliberate create carries a
     `# sqlite-create-ok: <reason>` comment on the connect line or in the
     comment block immediately above it. It is one line, it makes intent
     reviewable, and it is greppable. **Never put the marker on a connection
     that is merely safe** — a `mode=ro` reader annotated as a deliberate
     create is worse than an unmarked bug, because the next reader believes
     the label. That mistake was made once by a checker pointed at this
     repo, and it is the reason this sentence is here.

5. **`render_candles(bars, out_path, title) -> Path` is the entire chart
   interface.** Nothing outside `src/render/chart.py` may import anything
   else from that module. If you replace matplotlib with something else,
   rewrite this one file and keep the signature — every caller
   (`src/cli.py`, `verify.sh`) depends on nothing but this function existing
   and behaving as documented in `docs/ARCHITECTURE.md`.

6. **`ax.add_patch()` / `ax.add_line()` need explicit axis limits.** If you
   touch `chart.py` and add more low-level matplotlib artists, remember
   they don't participate in autoscale — the function will render
   successfully and produce a blank image otherwise. This bit this repo
   once already; see `docs/TROUBLESHOOTING.md`.

7. **Timestamps are naive IST wall-clock strings everywhere in this repo's
   internal pipeline**, format `"YYYY-MM-DD HH:MM:SS"`, no tzinfo attached.
   `src/bars.py::format_ist_timestamp()` is the one place a raw API
   timestamp gets converted; everything downstream treats the string as IST
   by convention, not by type. Don't introduce timezone-aware `datetime`
   objects into the `Bar` pipeline — you'll create a comparison bug the type
   system won't catch.

8. **The session close is exclusive**, enforced by
   `src/market/session.py::is_market_session_timestamp()`. A bar stamped
   15:30:00 is not a valid spot candle (the last one is 15:29:00). Both
   source brokers have been observed sending one anyway — this filter
   exists specifically to catch that, don't relax it.

9. **`session_minute_count()` and friends are derived from
   `SESSION_RULES`, never hardcoded.** If NSE session timing ever changes,
   add a new `SessionRule` with an `effective_from` date — do not edit an
   existing rule in place (that retroactively changes what the code
   believes about dates already stored) and do not hardcode a bar count
   anywhere.

10. **The three-stage filtering in the collect path is intentionally
    redundant**, not three attempts at the same thing by accident: the
    session filter in `bars.py`, the re-filter in `db/writer.py`, and the
    date-prefix filter in `collect.py` each guard a different call shape.
    See `docs/ARCHITECTURE.md`'s "Three-stage filtering" section before
    removing any of them on the assumption another one already covers it.

11. **`SpotBarWriter.upsert_spot_bars` must stay idempotent.** It compares
    against what's already stored (1e-6 float tolerance) and only writes
    rows that actually changed. Don't replace this with a naive
    `INSERT OR REPLACE` — that would make every re-run look like a fresh
    write, and there'd be no way to tell "this run changed 3 bars" from
    "this run touched nothing".

12. **Every dependency in `requirements.txt` is pinned to an exact
    version**, with a comment explaining its provenance (either "matches
    the source system's installed venv" or "resolved fresh, no history to
    defend"). Keep both discipline and the comment style when adding a
    dependency — see that file's own header.

13. **No real market data ships in this repo, ever.** Tests use
    `tests/synthetic.py`'s generator. If you add a test that needs bars,
    generate them; don't check in a CSV or a pre-populated `.db` file.

14. **Tests must not touch the network.** `tests/conftest.py`'s
    `_no_outbound_network` fixture blocks `urllib.request.urlopen`
    automatically for every test. A test that genuinely needs the network
    marks itself `@pytest.mark.network` (none currently do — `auth`'s
    `exchange_auth_code()` and `collect`'s Upstox calls are both
    network-touching and neither has a test that exercises the real call).

15. **Never write a real credential, token, or path to a personal
    credential store into any file in this repo.** `.env` is gitignored;
    `.env.example` holds named placeholders and comments only.

## Conventions

- **Module boundaries and dependency direction**: see
  `docs/ARCHITECTURE.md`'s module map. Dependencies point strictly downward
  through `cli.py → collect.py → {db.writer, market.*, upstox.*} → bars.py
  → market.session → market.calendar`. Don't introduce a cycle.
- **Docstrings explain WHY, not WHAT**, and where code was reduced from the
  source system, they say what was cut and why. Follow that pattern for new
  reductions or additions — a comment justifying a non-obvious choice is
  worth more here than a description of what the next line does.
- **Every CLI command validates its own preconditions before doing real
  work** (`_require_db_path`, the token check in `_cmd_collect`) and exits
  with a specific, actionable message rather than letting an exception
  surface from three layers down. Keep that pattern for new commands.
- **Gate**: `ruff check .` and `pytest -q` both clean before considering
  anything done. Neither is configured with a custom rule file yet (ruff
  runs on its defaults) — if you add one, follow the source system's
  practice of pinning the ruff version alongside it (see
  `requirements.txt`'s comment on why: two ruff releases reported 0 vs. 775
  findings on identical code).

## Where to add things

- **A new CLI verb**: `src/cli.py`, following the `_cmd_*` + subparser
  pattern already there. Keep the "validate preconditions, exit with a
  named error" shape from `_cmd_collect`/`_cmd_chart`/`_cmd_auth`.
- **A new segment (futures/options)**: this is a significant expansion, not
  a small addition. This repo made three simplifications that are correct
  *only* because it is spot-only and single-source — a futures/options
  extension needs all three undone: (1) `Instrument` was collapsed to a
  plain `str` instrument key in `src/models.py`/`src/upstox/`, correct only
  because there's exactly one instrument to resolve; restore it as a real
  dataclass carrying expiry/strike/option-type. (2) `src/bars.py` collapsed
  the source's per_bar/cumulative/auto volume-mode handling to a direct
  pass-through, correct only because a spot index always has zero volume;
  restore the full three-mode logic for any instrument with real volume.
  (3) `src/db/schema.py` dropped the canonical-source-priority table and
  view, correct only with exactly one source ever writing; restore it if a
  second source is ever involved.
- **A second data source**: `src/upstox/` is the only broker package.
  Adding a second means reintroducing something like the source system's
  `source_id`-in-primary-key + canonical-view pattern (`spot_bars` already
  carries `source_id` in its primary key for exactly this reason, even
  though only `"upstox"` is ever written today) — don't just add a second
  adapter and assume one row per bar still holds.
- **A new chart backend**: rewrite `src/render/chart.py` in place, keep the
  `render_candles(bars, out_path, title) -> Path` signature. Nothing else
  should need to change.
