#!/usr/bin/env python3
"""
nifty-spot-collector launcher -- a local, browser-based front end for the
existing `nifty-spot` CLI. Stdlib only; binds 127.0.0.1 only; shells out to
the CLI as a subprocess and never imports its module code. See
docs/LAUNCHER.md for the full design and docs/CREDENTIALS.md for the
redirect-URI registration this depends on.

Run: python3 launcher/server.py [--port 8765] [--no-browser]
"""
from __future__ import annotations

import argparse
import errno
import json
import re
import shlex
import sys
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs

import core

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"
STATIC_FILES = {
    "/static/app.js": (STATIC_DIR / "app.js", "application/javascript; charset=utf-8"),
    "/static/style.css": (STATIC_DIR / "style.css", "text/css; charset=utf-8"),
}
STATE_CHANGING_PATHS = {"/api/env", "/api/setup/stream", "/api/collect/stream"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CALLBACK_DONE_PAGE = """<!doctype html><meta charset="utf-8">
<title>nifty-spot-collector</title>
<body style="font: 16px system-ui, sans-serif; max-width: 34em; margin: 4em auto; color: #222;">
<h1>Login captured</h1>
<p>You can close this tab and return to the launcher tab, where the result
should already be showing.</p>
</body>"""

CALLBACK_ERROR_PAGE = """<!doctype html><meta charset="utf-8">
<title>nifty-spot-collector</title>
<body style="font: 16px system-ui, sans-serif; max-width: 34em; margin: 4em auto; color: #222;">
<h1>Login did not complete</h1>
<p>{error}</p>
<p>Close this tab and try Connect again from the launcher tab.</p>
</body>"""

# Set once in main(); read by every Handler instance (one process, one port).
PORT = core.DEFAULT_PORT

# Single in-flight OAuth exchange at a time -- this is a single-user,
# localhost-only tool, so a global with a lock is enough; no session concept
# is needed. See docs/LAUNCHER.md for the full /callback flow.
_oauth_lock = Lock()
_oauth_result: dict = {"pending": False, "success": None, "transcript": ""}

# The one chart path /api/chart-file will ever serve -- tracked server-side
# so that endpoint never has to trust a client-supplied path. Reset to None
# whenever a new collect run starts, so a failed re-run doesn't keep serving
# a stale image with no indication it's stale.
_chart_lock = Lock()
_last_chart_path: Path | None = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "nifty-spot-launcher/1.0"

    # -- logging -----------------------------------------------------------

    def log_message(self, format: str, *args) -> None:
        # Rebuilt from self.path/self.command rather than the raw request
        # line in `args` -- /callback?code=... would otherwise put the
        # one-time OAuth authorization code into terminal scrollback.
        path_only = self.path.partition("?")[0] if hasattr(self, "path") else "?"
        extra = " ".join(str(a) for a in args[1:]) if len(args) > 1 else ""
        sys.stderr.write(
            f'{self.log_date_time_string()} {self.address_string()} '
            f'"{self.command} {path_only} {self.request_version}" {extra}\n'
        )

    # -- small response helpers ---------------------------------------------

    def _json(self, status: int, obj) -> None:
        data = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, status: int, html_text: str) -> None:
        data = html_text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _bytes(self, status: int, data: bytes, content_type: str, extra_headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _start_chunked(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _write_chunk(self, text: str) -> None:
        data = text.encode("utf-8")
        if not data:
            return
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _end_chunked(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    # -- request-origin guards -----------------------------------------------
    # The launcher holds broker credentials and runs an HTTP server; any
    # page open in the same browser while it's running can attempt requests
    # against it. Host-checking blocks DNS rebinding (a page on a rebound
    # hostname pointed at 127.0.0.1); Origin-checking blocks a same-machine
    # page from issuing a cross-origin fetch() that changes state.

    def _check_host(self) -> bool:
        host = self.headers.get("Host", "")
        if host != f"127.0.0.1:{PORT}":
            self._json(
                403,
                {"error": f"Rejected: Host header was {host!r}, expected 127.0.0.1:{PORT}. "
                          "This launcher only accepts requests addressed directly to it."},
            )
            return False
        return True

    def _check_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://127.0.0.1:{PORT}":
            self._json(
                403,
                {"error": f"Rejected: Origin header was {origin!r}, expected "
                          f"http://127.0.0.1:{PORT} or absent. A page from somewhere else "
                          "cannot make changes here."},
            )
            return False
        return True

    # -- routing --------------------------------------------------------------

    def do_GET(self) -> None:
        if not self._check_host():
            return
        path, _, query = self.path.partition("?")
        if path == "/":
            self._serve_index()
        elif path in STATIC_FILES:
            self._serve_static(path)
        elif path == "/api/status":
            self._json(200, build_status_payload())
        elif path == "/api/env":
            self._handle_get_env()
        elif path == "/api/oauth/login-url":
            self._handle_oauth_login_url()
        elif path == "/callback":
            self._handle_callback(query)
        elif path == "/api/oauth/result":
            with _oauth_lock:
                self._json(200, dict(_oauth_result))
        elif path == "/api/chart-file":
            self._handle_chart_file()
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if not self._check_host():
            return
        path = self.path.partition("?")[0]
        if path in STATE_CHANGING_PATHS and not self._check_origin():
            return
        if path == "/api/env":
            self._handle_post_env()
        elif path == "/api/setup/stream":
            self._handle_setup_stream()
        elif path == "/api/collect/stream":
            self._handle_collect_stream()
        else:
            self.send_error(404)

    # -- static ------------------------------------------------------------

    def _serve_index(self) -> None:
        self._bytes(200, INDEX_FILE.read_bytes(), "text/html; charset=utf-8")

    def _serve_static(self, path: str) -> None:
        filepath, content_type = STATIC_FILES[path]
        self._bytes(200, filepath.read_bytes(), content_type)

    # -- status / env --------------------------------------------------------

    def _handle_get_env(self) -> None:
        raw_text, _ = core.read_dotenv_file()
        self._json(
            200,
            {
                "path": str(core.ENV_PATH),
                "exists": raw_text is not None,
                "raw_text": raw_text,
                "fields": core.env_field_status(),
                "secret_keys": sorted(core.SECRET_KEYS),
                "default_redirect_uri": f"http://127.0.0.1:{PORT}/callback",
            },
        )

    def _handle_post_env(self) -> None:
        body = self._read_json_body()
        if body is None or not isinstance(body, dict):
            self._json(400, {"error": "Malformed JSON body."})
            return
        updates = {k: str(v) for k, v in body.items() if k in core.KNOWN_ENV_KEYS}
        token_value = updates.get(core.ACCESS_TOKEN_KEY, "").strip()
        if token_value and not core.looks_like_upstox_token(token_value):
            self._json(
                400,
                {"error": "Token does not look like a valid Upstox token (expected a JWT "
                          "starting with 'eyJ'). Paste the full token, not a placeholder."},
            )
            return
        raw_text = core.write_dotenv(updates)
        self._json(200, {"path": str(core.ENV_PATH), "raw_text": raw_text, "fields": core.env_field_status()})

    # -- OAuth ---------------------------------------------------------------

    def _handle_oauth_login_url(self) -> None:
        fields = core.env_field_status()
        client_id = fields[core.CLIENT_ID_KEY]["effective_value"]
        redirect_uri = f"http://127.0.0.1:{PORT}/callback"
        missing = [
            k for k in (core.CLIENT_ID_KEY, core.CLIENT_SECRET_KEY)
            if not fields[k]["effective_value"]
        ]
        login_url = core.build_login_url(client_id, redirect_uri) if client_id else None
        self._json(200, {"login_url": login_url, "redirect_uri": redirect_uri, "missing": missing})

    def _handle_callback(self, query: str) -> None:
        params = parse_qs(query)
        error = (params.get("error") or [None])[0]
        code = (params.get("code") or [None])[0]

        if error:
            with _oauth_lock:
                _oauth_result.update(pending=False, success=False, transcript=f"Upstox returned an error: {error}\n")
            self._html(200, CALLBACK_ERROR_PAGE.format(error=escape(f"Upstox returned an error: {error}")))
            return
        if not code:
            self._html(400, CALLBACK_ERROR_PAGE.format(error=escape("No authorization code in the redirect URL.")))
            return

        with _oauth_lock:
            _oauth_result.update(pending=True, success=None, transcript="")

        redirect_uri = f"http://127.0.0.1:{PORT}/callback"
        cmd = [core.resolve_python(), "-m", "src.cli", "auth", "--redirect-uri", redirect_uri]
        try:
            result = core.run_captured(cmd, input_text=code + "\n", timeout=30)
            transcript = f"$ {shlex.join(cmd)}\n\n{result['output']}"
            success = result["returncode"] == 0
        except Exception as exc:
            transcript = f"$ {shlex.join(cmd)}\n\n[launcher] Error: {exc}\n"
            success = False

        with _oauth_lock:
            _oauth_result.update(pending=False, success=success, transcript=transcript)

        self._html(200, CALLBACK_DONE_PAGE)

    # -- setup / collect streaming --------------------------------------------

    def _handle_setup_stream(self) -> None:
        try:
            # On Windows this wraps the script in Git Bash -- see
            # core.script_command(). Resolved BEFORE the chunked response
            # starts, so a missing bash is a clean 400 with an actionable
            # message rather than an error stranded mid-stream.
            cmd = core.script_command(core.REPO_ROOT / "setup.sh")
        except RuntimeError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._start_chunked(200)
        self._write_chunk(f"$ {shlex.join(cmd)}\n\n")
        returncode, aborted = self._stream_subprocess(cmd)
        if aborted:
            return
        self._write_chunk("\n__LAUNCHER_RESULT__" + json.dumps({"ok": returncode == 0, "returncode": returncode}) + "\n")
        self._end_chunked()

    def _stream_subprocess(self, cmd: list[str]):
        """Runs cmd via core.stream_command(), writing each line to the
        client live. Returns (returncode, aborted) -- aborted is True when
        the client disconnected mid-run (see stream_command()'s
        docstring); the caller should skip writing a result trailer in
        that case, since there's nothing left to write to."""
        try:
            returncode = core.stream_command(cmd, self.connection, self._write_chunk)
        except Exception as exc:
            try:
                self._write_chunk(f"\n[launcher] Error running command: {exc}\n")
            except OSError:
                pass
            self.close_connection = True
            return -1, False
        if returncode is None:
            self.close_connection = True
            return None, True
        return returncode, False

    def _handle_collect_stream(self) -> None:
        global _last_chart_path

        body = self._read_json_body()
        if body is None or not isinstance(body, dict):
            self._json(400, {"error": "Malformed JSON body."})
            return
        from_date = str(body.get("from", "")).strip()
        to_date = str(body.get("to", "")).strip()
        if not DATE_RE.match(from_date) or not DATE_RE.match(to_date):
            self._json(400, {"error": "from/to must both be YYYY-MM-DD."})
            return

        db_path = core.env_field_status()[core.DB_PATH_KEY]["effective_value"]
        if not db_path:
            self._json(400, {"error": "No database path configured -- set SPOT_DB_PATH on the Credentials card first."})
            return

        symbol = "NIFTY"  # the only symbol the CLI supports; not user-editable
        python = core.resolve_python()
        out_dir = core.REPO_ROOT / "launcher" / ".output"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / core.timestamped_filename(f"chart_{from_date}_{to_date}", ".svg")
        title = f"{symbol} {from_date} to {to_date}"

        collect_cmd = [python, "-m", "src.cli", "--db", db_path, "collect", "--symbol", symbol, "--from", from_date, "--to", to_date]
        chart_cmd = [python, "-m", "src.cli", "--db", db_path, "chart", "--symbol", symbol, "--from", from_date, "--to", to_date, "--out", str(out_path), "--title", title]

        with _chart_lock:
            _last_chart_path = None

        self._start_chunked(200)
        self._write_chunk(f"$ {shlex.join(collect_cmd)}\n\n")
        collect_rc, aborted = self._stream_subprocess(collect_cmd)
        if aborted:
            return

        chart_rc = None
        if collect_rc == 0:
            self._write_chunk(f"\n$ {shlex.join(chart_cmd)}\n\n")
            chart_rc, aborted = self._stream_subprocess(chart_cmd)
            if aborted:
                return
        else:
            self._write_chunk("\n[launcher] Skipping chart -- collect did not succeed.\n")

        chart_ok = chart_rc == 0
        if chart_ok:
            with _chart_lock:
                _last_chart_path = out_path

        self._write_chunk(
            "\n__LAUNCHER_RESULT__"
            + json.dumps({"collect_ok": collect_rc == 0, "chart_ok": chart_ok, "chart_path": str(out_path) if chart_ok else None})
            + "\n"
        )
        self._end_chunked()

    # -- chart file ----------------------------------------------------------

    def _handle_chart_file(self) -> None:
        # Deliberately ignores any query string -- this always serves the
        # one path this process itself just generated, never anything
        # client-supplied, so there is no path-traversal surface here.
        with _chart_lock:
            path = _last_chart_path
        if not path or not path.exists():
            self.send_error(404, "No chart has been generated yet.")
            return
        content_type = "image/svg+xml" if path.suffix.lower() == ".svg" else "image/png"
        self._bytes(200, path.read_bytes(), content_type, {"Cache-Control": "no-store"})


def build_status_payload() -> dict:
    """Runs verify.sh and layers on the handful of things it doesn't
    report (sqlite3 CLI presence, token expiry/precedence) -- see
    docs/LAUNCHER.md. Every fact that verify.sh itself can answer comes
    from parsing its actual output, not a reimplementation of its checks.
    """
    verify_path = core.REPO_ROOT / "verify.sh"
    try:
        # script_command() raises RuntimeError on Windows with no Git Bash;
        # caught by the same handler below, so its message lands in the
        # Status card's raw-output pane where the user will actually see it.
        result = core.run_captured(core.script_command(verify_path), timeout=60)
        raw_output = result["output"]
        exit_code = result["returncode"]
    except Exception as exc:
        raw_output = f"Could not run verify.sh: {exc}"
        exit_code = None

    parsed = core.parse_verify_output(raw_output)
    checks = parsed["checks"]

    def detail(label: str):
        c = core.find_check(checks, label)
        return c["detail"] if c else None

    def status(label: str):
        c = core.find_check(checks, label)
        return c["status"] if c else None

    row_count = None
    if status("Row count (spot_bars)") == "OK":
        row_detail = detail("Row count (spot_bars)") or ""
        try:
            row_count = int(row_detail.split()[0])
        except ValueError:
            row_count = None

    env_file_detail = detail(".env file") or ""
    env_file_path = env_file_detail[len("found at "):] if env_file_detail.startswith("found at ") else str(core.ENV_PATH)

    db_reachable_detail = detail("Database reachable") or ""

    return {
        "exit_code": exit_code,
        "raw_output": raw_output,
        "interpreter": parsed["interpreter"],
        "python_version": parsed["python_version"],
        "checks": checks,
        "deps_ok": status("Python dependencies") == "OK",
        "sqlite3_present": core.sqlite3_cli_present(),
        "env_file": {"exists": status(".env file") == "OK", "path": env_file_path},
        "db": {
            "path": detail("SPOT_DB_PATH resolved") if status("SPOT_DB_PATH resolved") == "OK" else None,
            "exists": status("Database reachable") == "OK" and "does not exist yet" not in db_reachable_detail,
            "row_count": row_count,
        },
        "token": core.token_precedence_status(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="nifty-spot-launcher")
    parser.add_argument("--port", type=int, default=core.DEFAULT_PORT, help=f"Port to listen on (default {core.DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser tab automatically")
    args = parser.parse_args()

    global PORT
    PORT = args.port

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                f"nifty-spot-launcher: port {PORT} is already in use.\n"
                "  Another process is using it, or a previous launcher run didn't exit cleanly.\n"
                "  Pass --port to use a different one, e.g.:\n"
                f"    python3 launcher/server.py --port {PORT + 1}\n\n"
                "  NOTE: the Upstox redirect URI is tied to the port. If you change it, you\n"
                "  must also update the Redirect URL registered on your Upstox app's\n"
                "  dashboard, and UPSTOX_REDIRECT_URI in your .env, to match exactly.",
                file=sys.stderr,
            )
            sys.exit(1)
        raise

    url = f"http://127.0.0.1:{PORT}/"
    print(f"nifty-spot-launcher: serving {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nnifty-spot-launcher: stopping.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
