# Troubleshooting

Organised by symptom — what you're actually seeing — not by which file is
responsible. Find the message closest to what's in front of you.

Run `./verify.sh` before anything else here. Its final "paste this into an
issue" block answers most of what the sections below ask you to check by
hand.

---

## "It worked yesterday, fails today"

**This is the single most common failure with this tool, by design.** The
Upstox access token expires every morning. Nothing in this repo refreshes
it automatically — there is no scheduled job, no background process, and
none should ever be added (see `docs/ARCHITECTURE.md`).

**Fix**: run `nifty-spot auth` (or, in Docker,
`docker compose run --rm collector python -m src.cli auth`) and go through
the login flow again — about 30 seconds. Then retry whatever you were
doing.

You'll see this specifically as either:

```
nifty-spot: UPSTOX_ACCESS_TOKEN is not set and no credential file was found. ...
```
(no token at all — including the very first time you run `collect`, before
you've ever run `auth`)

or, further along, once a request actually reaches Upstox:

```
Upstox rejected the access token (HTTP 401). The daily token has likely
expired -- generate a fresh one from the Upstox developer dashboard.
```
(the token exists but Upstox no longer accepts it — HTTP 403 gives the same
message)

**A third, sneakier variant, CONFIRMED 2026-08-09**: `nifty-spot auth`
prints "Token saved", and the very next `collect` still fails (or worse,
silently keeps working on a token that's actually stale) — with no error
telling you why. Cause: `UPSTOX_ACCESS_TOKEN` set directly in `.env`
**always wins** over whatever `auth` just saved to the credentials file, no
staleness check, no warning. If you ever used the "copy straight from the
Upstox dashboard" shortcut (`docs/CREDENTIALS.md`), that value is probably
still sitting in `.env` and silently shadowing every `auth` run since.
**Fix**: blank `UPSTOX_ACCESS_TOKEN` back out in `.env` once you're relying
on `auth` day to day. Run `./verify.sh` — the "UPSTOX_ACCESS_TOKEN or saved
credential" line now names which source it actually found a token in
(`env var` vs `saved credential file`), specifically to catch this.

---

## "`chart` said it wrote a file, but the image is blank"

A real bug, caught and fixed during this repo's own build: matplotlib's
`ax.add_patch()` / `ax.add_line()` — used to draw each candle's body and
wick — do not register with matplotlib's autoscale the way `ax.plot()`
does. Without an explicit y-axis range, the chart renders successfully,
writes a real, non-empty file, and shows nothing: matplotlib's default 0–1
axes with every candle drawn outside the visible area.

`src/render/chart.py::render_candles()` now computes the y-range from the
data explicitly, so this specific cause is fixed. If you still see a blank
or near-blank chart after `collect` has reported real rows written:

1. Run `./verify.sh` — its "Chart renders" check does a synthetic self-test
   independent of your data, and reports the output file size. A
   suspiciously small file (under 1KB) fails that check outright.
2. Check the date range you charted actually has stored bars:
   `./verify.sh`'s "Row count (spot_bars)" line, or query the database
   directly (`sqlite3 "$SPOT_DB_PATH" "SELECT COUNT(*) FROM spot_bars WHERE
   trade_date BETWEEN '...' AND '...'"`).
3. If you've modified `src/render/chart.py`: any call to `ax.add_patch()` or
   `ax.add_line()` needs an explicit `ax.set_xlim()`/`ax.set_ylim()`
   somewhere after it — see that file's comments at the point this was
   fixed.

---

## "`collect` (or `verify.sh`) ran successfully, but there are 0 rows — or a database file appeared that I didn't expect"

Two related but distinct causes, both examples of the failure class this
whole repo is built to catch: **a command exiting 0 is not proof it did
anything real.**

**0 rows, legitimate**: the date range you asked for genuinely has no
data yet, or is entirely weekends/holidays. `collect` prints
`0 bars returned` per date when this happens rather than staying silent —
check its output, not just its exit code.

**0 rows because `SPOT_DB_PATH` pointed somewhere unexpected, or was
unset**: every command refuses to run rather than falling back to a
default path — you'll see
`nifty-spot: no database path configured -- refusing to run` and exit
status 2, not a silent success. If you're instead seeing an empty database
at a path you didn't choose, check what `SPOT_DB_PATH` (or `--db`) actually
resolved to: `./verify.sh`'s "SPOT_DB_PATH resolved" line reports the exact
value the code is using, not what you think you set.

**A phantom empty file appeared at your configured path, before you ever
ran `collect`**: this was a real bug found and fixed during this repo's
build, in `src/market/calendar.py`. Looking up whether a date is an NSE
trading day used to call `sqlite3.connect()` on the configured database
path without checking it existed first — and `sqlite3.connect()` **creates**
a file that doesn't exist yet. Any code path that calls
`is_market_session_timestamp()` (which `collect`, `chart`, and even
`verify.sh`'s own self-test all do) could trigger this before you'd
collected anything. Fixed with an existence check before the connection is
opened — if you see this behaviour, you're either on an older copy of this
repo or have found a new code path with the same mistake; check
`src/market/calendar.py::_get_db_connection` first.

`./verify.sh`'s "Database reachable" check reports, explicitly, whether the
database file existed **before** the script touched anything — that's the
line to trust over "it ran and didn't error".

---

## "`nifty-spot: No NSE trading days between ... and ...`"

```
nifty-spot: No NSE trading days between 2026-06-06 and 2026-06-06. Check the
dates and that they aren't both weekends/holidays.
```

The date range you gave `collect` contains no NSE trading day — both dates
are weekends, both fall in the small `NSE_HOLIDAYS` list (2026 only — see
below), or `--from` is after `--to`. Check the actual calendar dates, not
just the string format. Exits with status 1.

This is a likely first-run mistake — a weekend or a holiday week picked
without checking the calendar — so `collect` prints this as a plain,
formatted message and exits, the same as every other failure path in this
CLI. It does not surface as a raw Python traceback.

If the range is in a year other than 2026 and you believe a date in it
should be a holiday: `NSE_HOLIDAYS` in `src/market/calendar.py` only has
2026 entries. Dates outside it fall back to a weekday-only check and log a
warning (`is_trading_day(...): no trading_calendar row and no NSE_HOLIDAYS
entry for ...`) rather than failing silently — check your logs for that
warning if a range you expected to skip a holiday didn't.

---

## `ModuleNotFoundError: No module named 'upstox_client'` (or `'matplotlib'`)

Dependencies aren't installed into the Python environment you're running.

- **Native install**: you're probably running the system `python3`
  instead of `.venv/bin/python`. Re-run `./setup.sh` if `.venv` doesn't
  exist yet, or invoke commands as `.venv/bin/python -m src.cli ...`
  explicitly. **On Windows that path is `.venv/Scripts/python.exe`**, not
  `.venv/bin/python` — a virtualenv uses a different layout there.
- **Docker**: shouldn't happen inside the built image —
  `requirements.txt` is installed as part of the `Dockerfile`, and this
  path (dependencies installing and importing cleanly) is confirmed working
  on macOS, 2026-08-09. If you see it anyway, the image likely needs
  rebuilding: `docker compose build --no-cache`.

`./verify.sh`'s "Python dependencies" check reports exactly which package
is missing.

---

## Windows: `setup.sh` says "python3 not found" but Python is installed

The python.org build — which is what `winget install Python.Python.3.12`
and `bootstrap-windows.ps1` install — provides `python.exe` and the `py`
launcher. It does **not** create a `python3`. `setup.sh` and `verify.sh`
now look for `python3` first and then `python`, so this should no longer
happen; if you're on an older checkout that only looked for `python3`,
that's the cause.

Related, same vintage: `setup.sh` used to build its virtualenv paths as
`.venv/bin/pip`, which only exists on macOS and Linux — Windows puts them
in `.venv/Scripts/`. Both scripts and the launcher now detect the layout
that actually exists. This went unnoticed for a while because Windows
testing never got past the prerequisite check to reach the virtualenv step.

---

## Windows: `.\bootstrap-windows.ps1` refuses to run

Two likely causes:

- **"running scripts is disabled on this system"** — PowerShell's default
  execution policy. Run
  `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force`
  first; that affects only the current window.
- **"This script must run as Administrator"** — exactly what it says.
  Close the window, open the Start menu, type `PowerShell`, right-click
  *Windows PowerShell*, and choose **Run as administrator**.

If a single package fails but the rest succeed, the summary at the end
names it and the script exits without rolling anything back. Re-running is
safe — already-installed packages are detected and skipped.

---

## A traceback mentioning `ZoneInfoNotFoundError` or `"No time zone found with key Asia/Kolkata"`

The system's timezone database isn't available to Python. This repo uses
`ZoneInfo("Asia/Kolkata")` in several places, and it raises **at import
time** on a slim environment that lacks it — before any of this repo's own
error handling can run.

- **Native install**: install the `tzdata` PyPI package
  (`pip install tzdata` — already in `requirements.txt`, so this should only
  happen if you installed dependencies some other way than `./setup.sh`), or
  ensure your OS has its own timezone database installed.
- **Docker**: the `Dockerfile` installs the OS `tzdata` package
  explicitly for exactly this reason, and this path is confirmed working on
  macOS, 2026-08-09. If you still hit this, the image build may have
  failed partway — check `docker compose build` output for errors during
  the `apt-get install` step.

`./verify.sh`'s "Timezone data" check catches this directly and names the
underlying exception.

---

## `sqlite3.OperationalError: database is locked`

Another process holds a write lock on the database — most likely you ran
two `collect` commands against the same `SPOT_DB_PATH` at the same time.
This repo does not coordinate concurrent writers; don't run `collect`
twice concurrently against the same database. Wait for the first to finish
and retry.

## `sqlite3.DatabaseError: file is not a database`

The file at `SPOT_DB_PATH` exists but isn't a SQLite database — it's
something else, or corrupted, or was truncated mid-write. `./verify.sh`
reports this exact condition as a `[FAIL]` on "Database reachable". If you
don't need whatever's currently at that path, move it aside and let
`collect` create a fresh one; if you do need it, that's a data-recovery
problem outside this repo's scope.

---

## `nifty-spot auth` fails partway through

- **Fails immediately, before printing a URL**: `UPSTOX_CLIENT_ID` or
  `UPSTOX_REDIRECT_URI` is unset. The error names which one.
- **Prints a URL, then fails after you paste something back**:
  `UPSTOX_CLIENT_SECRET` is unset, or the pasted text had no `code=`
  parameter and wasn't a bare code either. The error tells you which.
- **The login URL opens in the browser and Upstox shows an error page
  instead of a login screen** — **CONFIRMED symptom, 2026-08-09**:
  ```json
  {"status":"error","errors":[{"errorCode":"UDAPI100068","message":"Check
  your 'client_id' and 'redirect_uri'; one or both are incorrect.", ...}]}
  ```
  This fires *before* you ever log in or paste anything back — Upstox is
  rejecting the login URL itself. It names both `client_id` and
  `redirect_uri` without saying which is actually wrong. In the confirmed
  case, the cause was `UPSTOX_CLIENT_ID` — a placeholder/wrong value had
  been pasted into `.env` instead of the real one. **Fix**: go to the
  Upstox dashboard → Apps → Algo Trading → expand your app's row → compare
  the **API Key** shown there, character for character, against
  `UPSTOX_CLIENT_ID` in `.env` (Upstox's dashboard confusingly labels this
  field "API Key", not "Client ID"; it's a UUID, e.g.
  `f5f5f626-226f-41ad-8ee9-42dab7e53273` — a short value like `5VC69U` is a
  sign something else got pasted there by mistake). Separately confirm the
  **Redirect URL** field on the app's "Edit app" page matches
  `UPSTOX_REDIRECT_URI` exactly. See `docs/CREDENTIALS.md` for both fields
  in full.
- **Fails with an HTTP-shaped error after pasting a real code** (a real
  code, from a real login — `client_id`/`redirect_uri` both correct enough
  to reach this step): prints
  `nifty-spot: token exchange failed: Upstox rejected the token exchange
  (HTTP ...): {...Upstox's JSON error body...}`, and exits 2. **A traceback
  here instead of that formatted message is a bug** (fixed 2026-08-09 —
  `exchange_auth_code()` previously let a raw `HTTPError` propagate as
  `HTTP Error 403: Forbidden` with no body and no explanation; it now
  catches it and surfaces Upstox's actual error text). If you're seeing a
  traceback, you're on an older copy of this repo.

  **CONFIRMED root cause, 2026-08-09** — not a stale code, not a wrong
  secret (both were tried and ruled out first): the error body was a
  **Cloudflare** rejection, not an Upstox one —
  `"error_code":1010,"error_name":"browser_signature_banned","detail":"The
  site owner has blocked access based on your browser's signature."`
  `exchange_auth_code()`'s HTTP request had no `User-Agent` header, so
  Python's `urllib` sent its own default (`Python-urllib/3.x`) — a
  signature Cloudflare's bot-management widely blocklists on sight, unrelated
  to anything in your `.env` or how quickly you pasted the code. **Fixed**
  in `src/upstox/session.py` by setting the request's `User-Agent` to
  `upstox-python-sdk`'s own default (`Swagger-Codegen/1.0.0/python`) —
  proven to work against this exact Cloudflare-protected domain, since
  every successful `collect` request already goes through that SDK and was
  never blocked. If you still see this after pulling the fix, something
  about your network path (a corporate proxy stripping/rewriting headers,
  for instance) may be altering the request — not something this repo can
  detect from here.

**If you just need a token right now and `auth` is giving you trouble**:
Upstox's dashboard (Apps → Algo Trading → expand your app's row) may show
an **Access Token** field directly, labelled "Generated By You" with a copy
icon — confirmed 2026-08-09 to work when pasted straight into
`UPSTOX_ACCESS_TOKEN` in `.env`, no `auth` run required. See "Two ways to
get today's token" in `docs/CREDENTIALS.md`.

---

## Docker-specific symptoms

**Confirmed against a real Docker daemon on macOS, 2026-08-09** — `docker
compose build`, `auth`, `collect`, `chart` (with the bind-mount retrieval
trick below), and `docker compose up`'s `verify.sh` pass all work as
documented. Two real bugs surfaced along the way and are already fixed:
Compose was silently swallowing the exit-3 wrapper's shell variables via
its own `$VAR` interpolation (needed `$$` escaping), and `verify.sh`'s
chart self-test imported from `tests/`, which the image deliberately
doesn't ship.

**Still not confirmed, either way**: Windows and Linux entirely — nothing
below has been tried there. Two narrower things weren't isolated during
macOS testing either, since `UPSTOX_ACCESS_TOKEN` happened to be set in
`.env` throughout, which would mask both: whether `docker compose up`/`run`
actually fails cleanly with no `.env` present at all, and whether a token
`auth` saves (written to the `nifty_credentials` named volume, not the
container's writable layer) genuinely survives a `docker compose down` +
rebuild — reasoned from the volume being named and persistent, not directly
tested end to end.

- `chart`'s `--out` needs to be a path *inside the container* that something
  can actually reach afterward. `/data` (the named volume) renders
  successfully but leaves the file trapped inside Docker's own storage,
  unreachable from your host. **Confirmed working, 2026-08-09**: bind-mount
  your current directory in for just that one command and write there
  instead —
  ```bash
  docker compose run --rm -v "$(pwd):/host" collector python -m src.cli chart --from ... --to ... --out /host/chart.svg
  ```
  lands `chart.svg` directly in your working directory. See the README's
  Docker section for this in context.
