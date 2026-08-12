"""
Non-HTTP mechanics for the launcher: everything server.py needs that isn't
routing. Stdlib only -- see README.md's constraint that the launcher must
run before dependencies are installed, so it cannot itself require any.

This module never imports anything from src/ -- the launcher shells out to
the CLI as a subprocess and never imports its module code (see
docs/LAUNCHER.md). Some small pieces of logic are therefore intentionally
duplicated rather than shared, each flagged where it happens:

  - the .env parser mirrors src/config.py::load_dotenv()'s rules exactly
    (same precedence, same quote-stripping) -- this file's version returns
    a dict instead of mutating os.environ, since the launcher needs to
    inspect values, not just load them into its own process.
  - CREDENTIALS_PATH mirrors src/upstox/session.py's constant.
  - looks_like_upstox_token() mirrors the same-named function there.

If any of those three ever change shape in src/, this file needs a matching
update -- there is no automated check that would catch drift, only this
comment.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import select
import shutil
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
# A virtualenv's interpreter lives in bin/ on macOS/Linux and Scripts/ on
# Windows. Listed in the same order verify.sh checks them, so the launcher
# and verify.sh can never disagree about which interpreter is in use.
VENV_PYTHON_CANDIDATES = (
    REPO_ROOT / ".venv" / "bin" / "python",
    REPO_ROOT / ".venv" / "Scripts" / "python.exe",
    REPO_ROOT / ".venv" / "Scripts" / "python",
)

# Mirrors src/upstox/session.py::CREDENTIALS_PATH -- see module docstring.
CREDENTIALS_PATH = Path.home() / ".config" / "nifty-spot-collector" / "upstox_credentials.json"

# The five variables this repo reads, and the ones the launcher's
# Credentials/Connect cards can write. Order here is display order.
DB_PATH_KEY = "SPOT_DB_PATH"
CLIENT_ID_KEY = "UPSTOX_CLIENT_ID"
CLIENT_SECRET_KEY = "UPSTOX_CLIENT_SECRET"
REDIRECT_URI_KEY = "UPSTOX_REDIRECT_URI"
ACCESS_TOKEN_KEY = "UPSTOX_ACCESS_TOKEN"
KNOWN_ENV_KEYS = [DB_PATH_KEY, CLIENT_ID_KEY, CLIENT_SECRET_KEY, REDIRECT_URI_KEY, ACCESS_TOKEN_KEY]

# Masked-by-default in the UI. Not just genuine secrets (Secret, token) --
# Client ID is included too, on the reasoning that uniform masking is
# easier to reason about on camera than a per-field judgement call, even
# though Client ID isn't itself sensitive. See docs/LAUNCHER.md's
# "recording this tool" section for the one place this masking can't help:
# the OAuth login URL itself always carries the Client ID in plain text.
SECRET_KEYS = {CLIENT_ID_KEY, CLIENT_SECRET_KEY, ACCESS_TOKEN_KEY}

DEFAULT_PORT = 8765
IST = timezone(timedelta(hours=5, minutes=30))  # hardcoded, not zoneinfo -- see decode_token_expiry()

# Mirrors src/upstox/session.py::AUTH_DIALOG_URL -- see module docstring.
AUTH_DIALOG_URL = "https://api.upstox.com/v2/login/authorization/dialog"

_QUOTE_CHARS = "\"'"


# ---------------------------------------------------------------------------
# .env parsing / writing
# ---------------------------------------------------------------------------

def parse_dotenv_text(text: str) -> dict[str, str]:
    """Same KEY=VALUE rules as src/config.py::load_dotenv(): '#' comments,
    blank lines, and lines with no '=' are skipped; matching surrounding
    quotes are stripped. Returns every key found, last occurrence wins --
    unlike load_dotenv() this doesn't touch os.environ or apply any
    real-environment precedence, it just answers "what does the file say."
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTE_CHARS:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def read_dotenv_file() -> tuple[Optional[str], dict[str, str]]:
    """Returns (raw_text_or_None, parsed_values). None text means .env
    doesn't exist yet."""
    if not ENV_PATH.exists():
        return None, {}
    text = ENV_PATH.read_text(encoding="utf-8")
    return text, parse_dotenv_text(text)


def write_dotenv(updates: dict[str, str]) -> str:
    """Patch .env with `updates`, preserving every comment and every other
    line. Starts from the existing .env if there is one, else from
    .env.example (so a first save still carries all of .env.example's
    explanatory comments forward, not just five bare KEY=VALUE lines).

    For each key in `updates`, replaces its `KEY=...` line in place if one
    exists, appends a new `KEY=value` line at the end otherwise. Writes
    atomically (temp file + os.replace) and chmods 0600, the same
    permission src/upstox/session.py::save_token() uses for its own
    credential file -- this file now holds UPSTOX_CLIENT_SECRET, and
    potentially UPSTOX_ACCESS_TOKEN via the paste-a-token field.

    Returns the new file's full text.
    """
    if ENV_PATH.exists():
        base_text = ENV_PATH.read_text(encoding="utf-8")
    elif ENV_EXAMPLE_PATH.exists():
        base_text = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
    else:
        base_text = ""

    lines = base_text.splitlines()
    remaining = dict(updates)
    for i, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.partition("=")[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        for key, value in remaining.items():
            lines.append(f"{key}={value}")

    new_text = "\n".join(lines) + "\n"

    tmp = ENV_PATH.with_suffix(".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(ENV_PATH)
    return new_text


# ---------------------------------------------------------------------------
# Precedence: what's in .env vs. what's actually in effect
# ---------------------------------------------------------------------------

def env_field_status() -> dict[str, dict[str, Any]]:
    """For each of the five known keys: what .env says, what the real
    (shell) environment says, which one is actually in effect once
    src/config.py::load_dotenv() runs, and whether that's a silent
    conflict worth warning about.

    An empty real-environment value (`export SPOT_DB_PATH=`) is treated as
    unset here too, matching load_dotenv()'s own fix for that trap -- this
    function exists specifically so the launcher doesn't reintroduce the
    "I changed it in the UI and nothing happened" failure with a *second*
    silent-precedence bug of its own.
    """
    _, file_values = read_dotenv_file()
    result: dict[str, dict[str, Any]] = {}
    for key in KNOWN_ENV_KEYS:
        file_value = file_values.get(key) or None
        env_value = os.environ.get(key) or None
        effective_value = env_value if env_value is not None else file_value
        conflict = file_value is not None and env_value is not None and file_value != env_value
        result[key] = {
            "file_value": file_value,
            "env_value": env_value,
            "effective_value": effective_value,
            "effective_source": "shell environment" if env_value is not None else (".env file" if file_value is not None else None),
            "conflict": conflict,
        }
    return result


def token_precedence_status() -> dict[str, Any]:
    """The access token has a third source beyond .env-vs-shell: the saved
    credentials file src/upstox/session.py::save_token() writes after a
    successful Connect. src/upstox/session.py::_load_token() checks, in
    order: real env var, then .env's value (merged into the env by
    load_dotenv() before _load_token() ever runs), then this file. Exposes
    which source is actually in effect and which other sources exist but
    are being silently shadowed -- this is precisely the "CONFIRMED
    footgun" docs/CREDENTIALS.md already documents (an env-var or .env
    token overriding every fresh one Connect saves afterward), surfaced in
    the UI instead of requiring the user to have read that doc.

    Reads token values only long enough to compare and decode; never
    returns or logs a raw value.
    """
    fields = env_field_status()[ACCESS_TOKEN_KEY]
    env_value = fields["env_value"]
    file_value = fields["file_value"]
    saved_value = None
    if CREDENTIALS_PATH.exists():
        try:
            saved_value = (json.loads(CREDENTIALS_PATH.read_text()).get("access_token") or None)
        except (OSError, ValueError):
            saved_value = None

    if env_value is not None:
        effective_source, effective_token = "shell environment", env_value
    elif file_value is not None:
        effective_source, effective_token = ".env file", file_value
    elif saved_value is not None:
        effective_source, effective_token = "saved credential (from Connect)", saved_value
    else:
        effective_source, effective_token = None, None

    shadowed = []
    for label, value in ((".env file", file_value), ("saved credential (from Connect)", saved_value)):
        if label != effective_source and value is not None and value != effective_token:
            shadowed.append(label)

    return {
        "present": effective_token is not None,
        "effective_source": effective_source,
        "shadowed_sources": shadowed,
        **decode_token_expiry(effective_token),
    }


# ---------------------------------------------------------------------------
# JWT expiry -- decode only, never validate, never return the token itself
# ---------------------------------------------------------------------------

def decode_token_expiry(token: Optional[str]) -> dict[str, Any]:
    """{"expiry": ISO string or None, "expired": bool or None}. Decodes the
    JWT payload's `exp` claim only -- no signature check, this is not an
    authentication decision, just a label. Uses a hardcoded Asia/Kolkata
    (+05:30, no DST) offset rather than zoneinfo: this code must keep
    working on a bare system Python with no OS tzdata package installed,
    which is exactly the pre-setup.sh state the Status card needs to
    describe (see requirements.txt's own tzdata comment for why zoneinfo
    can raise ZoneInfoNotFoundError in that situation).
    """
    if not token:
        return {"expiry": None, "expired": None}
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        exp = payload["exp"]
        expiry_dt = datetime.fromtimestamp(exp, tz=IST)
        return {"expiry": expiry_dt.isoformat(), "expired": expiry_dt < datetime.now(IST)}
    except (IndexError, ValueError, KeyError, TypeError):
        return {"expiry": None, "expired": None}


def looks_like_upstox_token(token: str) -> bool:
    """Mirrors src/upstox/session.py::looks_like_upstox_token() -- see this
    file's module docstring for why it's duplicated rather than imported."""
    return token.startswith("eyJ") and token.count(".") >= 2 and len(token) > 100


def build_login_url(client_id: str, redirect_uri: str) -> str:
    """Mirrors src/upstox/session.py::get_login_url()'s URL-building --
    just string formatting, not an API call, so building it here (instead
    of shelling out just to print a URL) doesn't duplicate any real logic.
    The actual token exchange still always goes through the real `auth`
    command -- see server.py's /callback handler."""
    from urllib.parse import urlencode

    return f"{AUTH_DIALOG_URL}?{urlencode({'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri})}"


def sqlite3_cli_present() -> bool:
    """The sqlite3 CLI binary -- not used by any Python here (sqlite3 the
    stdlib module talks to the database file directly), but setup.sh
    checks for it as a prerequisite and the Status card mirrors that."""
    return shutil.which("sqlite3") is not None


# ---------------------------------------------------------------------------
# verify.sh output -> structured status
# ---------------------------------------------------------------------------

_CHECK_RE = re.compile(r"^\[(OK|FAIL|SKIP)\]\s+(.*)$")


def parse_verify_output(text: str) -> dict[str, Any]:
    """Turns verify.sh's own `[OK]/[FAIL]/[SKIP] label -- detail` lines
    into structured data -- this is deliberately a parser, not a
    reimplementation: every fact it reports is verify.sh's own answer,
    never recomputed here."""
    interpreter = None
    python_version = None
    checks: list[dict[str, str]] = []
    for line in text.splitlines():
        if line.startswith("Using interpreter:"):
            interpreter = line.split(":", 1)[1].strip()
            continue
        if line.startswith("Python:"):
            m = re.match(r"^Python:\s+(\S+)\s+\((.*)\)$", line)
            if m:
                python_version = m.group(1)
            continue
        m = _CHECK_RE.match(line)
        if m:
            label, _, detail = m.group(2).partition(" -- ")
            checks.append({"status": m.group(1), "label": label.strip(), "detail": detail.strip()})
    return {"interpreter": interpreter, "python_version": python_version, "checks": checks}


def find_check(checks: list[dict[str, str]], label: str) -> Optional[dict[str, str]]:
    return next((c for c in checks if c["label"] == label), None)


# ---------------------------------------------------------------------------
# Subprocess plumbing
# ---------------------------------------------------------------------------

def resolve_python() -> str:
    """Same rule as verify.sh: prefer the native-install virtualenv if one
    exists, else whatever Python 3 is on PATH.

    Checks both virtualenv layouts (bin/ on macOS/Linux, Scripts/ on
    Windows) and both interpreter names -- the python.org Windows build
    that `winget install Python.Python.3.12` provides is `python`, not
    `python3`, so a `python3`-only lookup finds nothing on a freshly
    bootstrapped Windows machine. Falls back to the literal string
    "python3" when nothing is found, so the resulting command still fails
    with a recognisable name rather than an empty argv[0]."""
    for candidate in VENV_PYTHON_CANDIDATES:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return found
    return "python3"


def find_bash() -> Optional[str]:
    """Locate a bash interpreter. Only meaningful on Windows, where Git Bash
    provides it -- and where `C:\\Program Files\\Git\\bin` is commonly NOT on
    PATH even though `git` itself is, because Git for Windows' default
    install puts only `Git\\cmd` there. So PATH is checked first, then the
    standard install locations."""
    found = shutil.which("bash")
    if found:
        return found
    candidates = [
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
    ]
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "Programs" / "Git" / "bin" / "bash.exe")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def script_command(script_path: "str | Path", is_windows: Optional[bool] = None) -> list[str]:
    """Build the argv for running one of this repo's .sh scripts.

    On macOS/Linux that's just the script itself -- its shebang does the
    work. On Windows it is NOT: there is no shebang support, so handing
    `setup.sh` straight to CreateProcess fails with "%1 is not a valid Win32
    application". The script has to be passed to Git Bash's `bash` instead.
    Backslashes are normalised to forward slashes because that's what the
    MSYS-based bash expects to receive.

    `is_windows` exists so both branches are testable off-Windows; it
    defaults to the real platform.
    """
    if is_windows is None:
        is_windows = os.name == "nt"
    if not is_windows:
        return [str(script_path)]

    bash = find_bash()
    if not bash:
        raise RuntimeError(
            "Git Bash not found, and Windows cannot run setup.sh / verify.sh "
            "directly (they are bash scripts, and Windows has no shebang "
            "support). Run bootstrap-windows.ps1 as Administrator to install "
            "Git -- which provides Git Bash -- then restart the launcher. "
            "See README.md."
        )
    return [bash, str(script_path).replace("\\", "/")]


def run_captured(cmd: list[str], *, input_text: Optional[str] = None, timeout: Optional[float] = None) -> dict[str, Any]:
    """Run `cmd` to completion, capturing stdout+stderr merged (never
    swallowing stderr). Used for short-lived calls only (the OAuth
    exchange) -- anything long-running streams instead, see
    iter_subprocess_lines()."""
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {"returncode": proc.returncode, "output": (proc.stdout or "") + (proc.stderr or "")}


def _peer_disconnected(client_socket: socket.socket) -> bool:
    """True if the HTTP client has closed its end. A readable socket that
    yields zero bytes on a peek is a closed peer; readable-with-data is
    just a pipelined request we don't care about here.

    select() is called on the SOCKET ONLY. On Windows select() accepts
    nothing but sockets -- passing it a pipe raises -- which is why the
    subprocess's stdout is read on a separate thread instead of being
    handed to the same select() call. See stream_command()."""
    try:
        ready, _, _ = select.select([client_socket], [], [], 0)
    except (OSError, ValueError):
        return True
    if not ready:
        return False
    try:
        return client_socket.recv(1, socket.MSG_PEEK) == b""
    except BlockingIOError:
        return False
    except OSError:
        return True


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Kill `proc` AND everything it started.

    proc.terminate() alone is not enough here and testing proved it: the
    thing being run is `setup.sh`, and the process that actually matters --
    `pip install`, minutes long -- is its CHILD. Signalling only the direct
    child leaves bash dead and pip running, orphaned, exactly the leak this
    whole code path exists to prevent. (An earlier version of this fix
    appeared to pass only because the test happened to disconnect during
    `python -m venv`, before pip had been started at all.)

    POSIX: the child is given its own process group via start_new_session,
    so the whole group can be signalled at once. Windows: taskkill /T walks
    and kills the tree, since there is no process-group signal equivalent.
    """
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, check=False,
        )
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError):
        proc.kill()
        return
    for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, 2)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def stream_command(cmd: list[str], client_socket: socket.socket, on_line: Callable[[str], None]) -> Optional[int]:
    """Run `cmd`, calling `on_line(text)` for each stdout+stderr line
    (merged, so nothing is swallowed) as it arrives -- live, not buffered.

    Watches the client connection at the same time, because of a real bug
    caught during this launcher's own testing: a quiet subprocess
    (`pip install --quiet` can go tens of seconds with no output) leaves
    the handler blocked waiting for the *next line*, with nothing to
    write -- so a closed browser tab was never discovered (the usual "the
    next write raises" signal never arrives, because there is no next
    write) and `pip install` kept running to completion as an orphaned
    background process.

    Output is drained on a reader thread into a queue rather than
    select()ed alongside the socket, because select() on Windows accepts
    only sockets -- the obvious single-select() version works on
    macOS/Linux and raises OSError on Windows, taking both the Install and
    Collect streams down with it. A queue with a timeout gives the same
    "wake up regularly even when the child is silent" behaviour on every
    platform.

    Returns the subprocess's exit code, or None if the client disconnected
    first (in which case the caller should treat the run as aborted, not
    failed). Either way, the child is terminated before this returns if
    it's somehow still running -- never left behind.
    """
    # start_new_session / CREATE_NEW_PROCESS_GROUP put the child in its own
    # process group so _terminate_tree() can take down its descendants too
    # -- see that function for why the direct child is not the one that
    # matters.
    group_kwargs: dict[str, Any] = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
        else {"start_new_session": True}
    )
    proc = subprocess.Popen(
        cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        **group_kwargs,
    )
    assert proc.stdout is not None

    lines: "queue.Queue[Optional[str]]" = queue.Queue()

    def _drain() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                lines.put(line)
        finally:
            lines.put(None)  # sentinel: stdout reached EOF

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    try:
        while True:
            if _peer_disconnected(client_socket):
                return None
            try:
                line = lines.get(timeout=1.0)
            except queue.Empty:
                continue  # child is just quiet; loop re-checks the client
            if line is None:
                break
            on_line(line)
        proc.wait(timeout=30)
        return proc.returncode
    finally:
        _terminate_tree(proc)


def timestamped_filename(prefix: str, suffix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}{suffix}"
