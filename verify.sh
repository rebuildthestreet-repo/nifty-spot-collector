#!/usr/bin/env bash
# nifty-spot-collector -- the highest-value file in this repo.
#
# The failure mode this exists to catch is not "the script errored". It's
# "it seemed to work but nothing happened" -- specifically, the exact thing
# that has bitten the system this repo was extracted from TWICE: an
# unconfigured database path silently falling back to a default that SQLite
# happily creates, so a command runs, reports "0 rows", and exits 0 looking
# exactly like success. Every check below is written to distinguish
# "correctly configured and legitimately empty" from "never found the
# config in the first place" -- see the "Database reachable" check
# specifically, which reports whether the database file existed BEFORE this
# script touched anything.
#
# Never prints a secret. Token/credential checks report presence only
# (yes/no), never a value.
#
# Exit code contract -- the only thing worth checking if you're scripting
# against this instead of reading its output:
#
#   0   fully green -- everything checked is present and working.
#   1   something is broken -- at least one check genuinely FAILed.
#   2   verify.sh itself could not even start (no python3 found, or src/
#       isn't importable).
#   3   configured correctly, but no Upstox token is set -- one `auth` run
#       away from fully working. NOT an error: the access token expires
#       every morning, so this is a state you land in daily, by design, not
#       a sign anything is wrong. See docker-compose.yml for how the Docker
#       path treats this as a successful `docker compose up`, not a failed
#       build.
#
# Usage: ./verify.sh [--online]
#
# Network checks are opt-in (D-122). "Historical API reachable" is the only
# check that makes a live call, and it used to fire unprompted whenever a
# token was present -- which is every time this script runs on camera right
# after the operator's daily token refresh. Off by default now; pass
# --online or set VERIFY_ALLOW_NETWORK=1 to run it.
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default OFF, never default-on -- see the header above and D-122.
ALLOW_NETWORK="${VERIFY_ALLOW_NETWORK:-0}"
for _arg in "$@"; do
    case "$_arg" in
        --online) ALLOW_NETWORK=1 ;;
    esac
done

# Prefer the native install's virtualenv if one exists; otherwise fall back
# to whatever Python 3 is on PATH (the Docker image, where dependencies are
# installed at the system level).
#
# A virtualenv puts its interpreter in bin/ on macOS/Linux and Scripts/ on
# Windows, and the python.org Windows build (what bootstrap-windows.ps1
# installs via winget) provides `python`, not `python3`. Checking only
# .venv/bin/python + python3 -- what this used to do -- meant that on
# Windows this script silently fell through to "no python3 found" even with
# a working venv sitting right there.
PY=""
for _cand in \
    "${REPO_DIR}/.venv/bin/python" \
    "${REPO_DIR}/.venv/Scripts/python.exe" \
    "${REPO_DIR}/.venv/Scripts/python"
do
    if [ -x "$_cand" ]; then
        PY="$_cand"
        break
    fi
done

if [ -z "$PY" ]; then
    for _cand in python3 python; do
        if command -v "$_cand" >/dev/null 2>&1 \
           && "$_cand" -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
            PY="$(command -v "$_cand")"
            break
        fi
    done
fi

if [ -z "${PY:-}" ]; then
    echo "[FAIL] No Python 3 found (looked for 'python3' and 'python' on PATH,"
    echo "       and for a virtualenv at ${REPO_DIR}/.venv)."
    echo "  Run ./setup.sh first (native install), or use Docker."
    echo "  On Windows, run bootstrap-windows.ps1 first -- see README.md."
    exit 1
fi

echo "nifty-spot-collector verify"
echo "============================"
echo "Using interpreter: ${PY}"
echo

NIFTY_VERIFY_REPO_DIR="$REPO_DIR" NIFTY_VERIFY_ALLOW_NETWORK="$ALLOW_NETWORK" "$PY" - <<'PYEOF'
import os
import platform
import sys
import tempfile
from datetime import datetime

REPO_DIR = os.environ.get("NIFTY_VERIFY_REPO_DIR", ".")
sys.path.insert(0, REPO_DIR)

# Opt-in gate for the one network call in this script (D-122). Set by the
# bash wrapper above from VERIFY_ALLOW_NETWORK / --online; defaults off.
ALLOW_NETWORK = os.environ.get("NIFTY_VERIFY_ALLOW_NETWORK", "0") == "1"

results = []  # (status, label, detail) -- status in OK/FAIL/SKIP


def check(status, label, detail=""):
    results.append((status, label, detail))
    tag = {"OK": "[OK]  ", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[status]
    line = f"{tag} {label}"
    if detail:
        line += f" -- {detail}"
    print(line)


# 1. Dependencies -----------------------------------------------------------
missing_deps = []
for mod_name, pip_name in [("upstox_client", "upstox-python-sdk"), ("matplotlib", "matplotlib")]:
    try:
        __import__(mod_name)
    except ImportError:
        missing_deps.append(pip_name)
if missing_deps:
    check("FAIL", "Python dependencies", f"missing: {', '.join(missing_deps)} -- run ./setup.sh or pip install -r requirements.txt")
else:
    check("OK", "Python dependencies", "upstox-python-sdk, matplotlib importable")

# 2. tzdata / Asia/Kolkata ---------------------------------------------------
try:
    from zoneinfo import ZoneInfo
    ZoneInfo("Asia/Kolkata")
    check("OK", "Timezone data", "Asia/Kolkata resolvable")
except Exception as exc:
    check("FAIL", "Timezone data", f"{type(exc).__name__}: {exc} -- install the OS tzdata package, or `pip install tzdata`")

# src/ must be importable before anything else below means anything.
try:
    from src.config import load_dotenv, resolve_db_path, resolve_upstox_token
except Exception as exc:
    check("FAIL", "Import src.config", f"{type(exc).__name__}: {exc}")
    print("\nCannot continue -- src/ is not importable. Fix the above, then re-run.")
    sys.exit(2)

# Load .env the same way `nifty-spot` itself now does (src/config.py::load_dotenv)
# -- real environment variables still win over anything in the file. Done
# here, before any check below reads an env var, so this script's own
# answers match what a plain `./verify.sh` (no manual sourcing) actually sees.
load_dotenv()

# 3. Config file --------------------------------------------------------
env_file_path = os.path.join(REPO_DIR, ".env")
if os.path.exists(env_file_path):
    check("OK", ".env file", f"found at {env_file_path}")
else:
    check("SKIP", ".env file", "not found -- fine if variables were set another way (Docker's env_file:, a shell profile, etc.)")

# 4. Required env vars, and where SPOT_DB_PATH actually resolved to --------
db_path = resolve_db_path()
if db_path:
    check("OK", "SPOT_DB_PATH resolved", db_path)
else:
    check("FAIL", "SPOT_DB_PATH resolved", "not set -- every command refuses to run. See .env.example.")

def _resolve_token_including_saved_file():
    # resolve_upstox_token() alone only checks UPSTOX_ACCESS_TOKEN -- the
    # env var. It does NOT check the credentials file `auth` actually
    # saves to (~/.config/nifty-spot-collector/upstox_credentials.json),
    # even though this check's own label says "or saved credential" and
    # src/upstox/session.py::_load_token() -- what `collect` actually
    # calls -- checks both. Confirmed as a real false-negative 2026-08-09:
    # the normal workflow (auth saves to the file, .env's
    # UPSTOX_ACCESS_TOKEN stays blank) would have reported [SKIP] "not
    # set" here despite `collect` working fine. Mirrors _load_token()'s
    # own env-then-file order without importing it directly, since that
    # function raises instead of returning None on nothing found.
    env_token = resolve_upstox_token()
    if env_token:
        return env_token, "env"
    try:
        from src.upstox.session import CREDENTIALS_PATH
        import json
        if CREDENTIALS_PATH.exists():
            with open(CREDENTIALS_PATH) as f:
                saved = json.load(f).get("access_token")
            if saved:
                return saved, "file"
    except Exception:
        pass
    return None, None


token, token_source = _resolve_token_including_saved_file()
check(
    "OK" if token else "SKIP",
    "UPSTOX_ACCESS_TOKEN or saved credential",
    (
        f"present ({'env var' if token_source == 'env' else 'saved credential file'})"
        if token else
        "not set -- run `python -m src.cli auth`, or `collect` will refuse to run"
    ),
)

app_creds_present = all(os.environ.get(v) for v in ("UPSTOX_CLIENT_ID", "UPSTOX_CLIENT_SECRET", "UPSTOX_REDIRECT_URI"))
check(
    "OK" if app_creds_present else "SKIP",
    "Upstox app credentials (CLIENT_ID / CLIENT_SECRET / REDIRECT_URI)",
    "all set" if app_creds_present else "one or more unset -- only `auth` needs these",
)

# 5. Historical API reachable -- a REAL, live call, only if a token exists
# AND network checks are opted in (VERIFY_ALLOW_NETWORK=1 or --online; off
# by default, D-122). This is the only check here that touches the network.
# It makes one read-only historical request for a fixed past date and
# writes nothing to your database. It used to run unprompted whenever a
# token was present -- on camera, right after a fresh token, that fired a
# live authenticated-shaped call in the middle of a recording. Opt-in now.
#
# NOT called "Token accepted by Upstox" (what it was originally, and what
# it still sounds like it should prove) because it doesn't, and confirming
# that was itself the finding: on 2026-08-09, plain curl against this exact
# endpoint with no Authorization header, an empty one, and a garbage token
# ALL returned real 200 data with real candles. Upstox's historical-candle
# endpoint for NIFTY spot appears not to require authentication at all --
# presumably deliberate on their side, index-level OHLC treated as public
# reference data unlike account-specific trading endpoints. A confirmed
# TLS certificate for api.upstox.com during that test ruled out a local
# interception explaining it away.
#
# So this check genuinely can confirm the network path, DNS, TLS, and your
# configured NIFTY instrument key are all correct -- it just can't tell you
# whether your token specifically is valid, because Upstox itself doesn't
# appear to check. Don't rely on this being permanent: Upstox could tighten
# this without notice, and it may not hold for every account/region. The
# CLI still requires a token to be configured either way, both because the
# shape check catches obvious paste mistakes and because that requirement
# costs nothing to keep even where it isn't strictly enforced server-side.
PROBE_DATE = "2026-06-01"  # a known NSE trading day, safely in the past
if not ALLOW_NETWORK:
    check("SKIP", "Historical API reachable",
          "SKIPPED -- network checks are opt-in (set VERIFY_ALLOW_NETWORK=1 or --online)")
elif token:
    try:
        from src.upstox.adapter import UpstoxSpotAdapter
        from src.upstox.instruments import resolve_index

        adapter = UpstoxSpotAdapter()
        instrument_key = resolve_index("NIFTY")
        # verify-network-ok: the one live call this script ever makes,
        # reached only when ALLOW_NETWORK is explicitly on (D-122).
        bars = adapter.fetch_spot_bars("NIFTY", instrument_key, PROBE_DATE, PROBE_DATE)
        check("OK", "Historical API reachable", f"call for {PROBE_DATE} succeeded, {len(bars)} bar(s) -- does not confirm token validity, see comment above")
    except RuntimeError as exc:
        check("FAIL", "Historical API reachable", str(exc))
    except Exception as exc:
        check("FAIL", "Historical API reachable", f"{type(exc).__name__}: {exc}")
else:
    check("SKIP", "Historical API reachable", "no token configured -- nothing to probe")

# 6-8. Database: reachable, expected tables, row count ----------------------
# THE central check this script exists for: does the database file already
# exist, BEFORE anything here opens it? Answered first, on purpose, so
# nothing below can make this file's own answer wrong by having already
# created the thing it's supposed to be reporting on.
pre_existed = bool(db_path) and os.path.exists(db_path)

if not db_path:
    check("SKIP", "Database reachable", "no SPOT_DB_PATH -- nothing to check")
    check("SKIP", "Expected tables present", "no database configured")
    check("SKIP", "Row count", "no database configured")
elif not pre_existed:
    check("OK", "Database reachable", f"{db_path} does not exist yet -- expected before your first `collect`, not an error")
    check("SKIP", "Expected tables present", "database not created yet")
    check("SKIP", "Row count", "database not created yet")
else:
    try:
        from src.db.reader import SpotBarReader

        reader = SpotBarReader(db_path)
        try:
            tables = {row[0] for row in reader.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            # Only reported OK here, after a query has actually succeeded --
            # not before, since SpotBarReader opens its connection lazily on
            # first use and printing this any earlier would risk a stale "OK"
            # sitting above a "FAIL" for the exact same check below.
            check("OK", "Database reachable", f"{db_path} pre-existed")
            expected_tables = {"spot_bars", "instruments", "trading_calendar"}
            missing_tables = expected_tables - tables
            if missing_tables:
                check("FAIL", "Expected tables present", f"missing: {', '.join(sorted(missing_tables))} -- schema may be from an older version")
            else:
                check("OK", "Expected tables present", ", ".join(sorted(expected_tables)))

            row_count = reader.conn.execute("SELECT COUNT(*) FROM spot_bars").fetchone()[0]
            check("OK", "Row count (spot_bars)", f"{row_count} -- 0 is a legitimate answer if you haven't run `collect` yet")
        finally:
            reader.close()
    except Exception as exc:
        check("FAIL", "Database reachable", f"{db_path} exists but could not be read: {type(exc).__name__}: {exc}")

# 9. Chart renders ------------------------------------------------------
# A synthetic self-test, independent of whether you've collected any real
# data yet -- this checks the RENDER PATH works, not your data. Written the
# way it is because of a real bug caught while building this repo:
# render_candles() can return successfully, write a real file, and that file
# can contain nothing visible -- see docs/TROUBLESHOOTING.md. A file-size
# floor is a crude proxy for "probably not blank", not a guarantee -- it
# does not open the image.
#
# The sample bars are generated INLINE, not imported from tests/synthetic.py
# -- confirmed 2026-08-09 that importing from `tests` raises
# ModuleNotFoundError inside the Docker image, which deliberately COPYs
# src/ only, never tests/ (shipping test code in a deployed image is the
# wrong call, not a bug to work around by shipping it anyway). verify.sh
# has to work standalone in exactly this kind of minimal environment --
# that's the whole point of it -- so it can't depend on the test suite.
try:
    from datetime import datetime, timedelta

    from src.render.chart import render_candles

    def _tiny_synthetic_bars():
        start = datetime(2026, 6, 1, 9, 15, 0)
        price = 24000.0
        bars = []
        for i in range(10):
            ts = start + timedelta(minutes=i)
            close = price + (3.0 if i % 2 == 0 else -2.0)
            bars.append({
                "ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": price,
                "high": max(price, close) + 1.5,
                "low": min(price, close) - 1.5,
                "close": close,
                "volume": 0.0,
            })
            price = close
        return bars

    probe_bars = _tiny_synthetic_bars()
    with tempfile.TemporaryDirectory() as tmp:
        out_path = render_candles(probe_bars, os.path.join(tmp, "verify_probe.svg"), "verify.sh self-test")
        size = os.path.getsize(out_path)
        if size < 1024:
            check("FAIL", "Chart renders", f"output suspiciously small ({size} bytes) -- may be blank")
        else:
            check("OK", "Chart renders", f"self-test wrote a {size}-byte SVG from synthetic data (not yours)")
except Exception as exc:
    check("FAIL", "Chart renders", f"{type(exc).__name__}: {exc}")

# ---------------------------------------------------------------------------
# Diagnostic block -- paste this into an issue. No secrets in it: every
# credential-shaped check above reports presence only, never a value.
# ---------------------------------------------------------------------------
print()
print("=" * 70)
print("Paste everything below this line into a GitHub issue:")
print("=" * 70)
print(f"nifty-spot-collector verify.sh -- {datetime.now().isoformat(timespec='seconds')}")
print(f"OS: {platform.platform()}")
print(f"Python: {platform.python_version()} ({sys.executable})")
for status, label, detail in results:
    line = f"  [{status}] {label}"
    if detail:
        line += f" -- {detail}"
    print(line)
print("=" * 70)

fail_count = sum(1 for status, _, _ in results if status == "FAIL")
if fail_count:
    exit_code = 1
elif not token:
    # Not a failure -- see this script's header for the full exit-code
    # contract. The access token expires every morning, so "configured
    # correctly, but no token yet" is a state every user lands in daily,
    # not a sign something's wrong. Printed explicitly, not left for
    # someone reading only `$?` to have to infer from a [SKIP] line above.
    print("\nResult: configured correctly, no token yet -- run `auth`, this is normal (exit 3).")
    exit_code = 3
else:
    exit_code = 0

sys.exit(exit_code)
PYEOF

exit $?
