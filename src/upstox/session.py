"""
Load the daily Upstox access token, build an authenticated API client, and
(restored after an earlier over-trim -- see the module-level correction
below) help a first-time user obtain that token in the first place.

Reduced from brokers/upstox/upstox_session.py (the source system). Dropped:
`token_kind` ("analytics" vs "access" -- the source's own comments record
that the long-lived analytics token was removed in 2026 because Upstox's v3
market-data feed rejects it; only the daily access token is real now) and
`check_session` (pings the instrument resolver this repo doesn't have, since
src/upstox/instruments.py hardcodes the key instead of downloading a
contract master to scan -- see that file's docstring).

CORRECTION, not part of the original reduction: an earlier pass through this
file also dropped `load_credentials`, `get_login_url` and `exchange_auth_code`
as "unused by the historical-collection path" -- true, but wrong to cut,
because those three are the ONLY way this repo can help a first-time user
who has no token yet obtain one. Collection needs a token; nothing in the
repo could get you one. Restored here, and wired to `nifty-spot auth` in
src/cli.py.

What this restores is the OAuth AUTHORIZATION-CODE EXCHANGE -- a signed HTTP
POST to Upstox that trades a one-time code for an access token. It is NOT an
automated refresh, and must never become one: the owner's daily ritual is to
open the login URL in a browser, log in by hand, and paste back either the
redirect code or the whole redirect URL. That manual login step is real and
cannot be scripted around -- it is the actual safety boundary, not a
convenience this repo is choosing not to build.

Credential resolution order for the ACCESS TOKEN: UPSTOX_ACCESS_TOKEN env var
first, then a JSON file under this project's own config directory
(~/.config/nifty-spot-intraday-collector/, not the source's ~/.config/marketmaster/,
so installing this repo can never read or collide with credentials written
by the system it was extracted from).

The APP REGISTRATION values (client_id/client_secret/redirect_uri) needed
only by `auth` are env-var only, with no file fallback -- unlike the source,
which also reads a JSON credentials file for these. They change rarely
enough (once, when you register the app on Upstox) that adding a second
persistence path for them was judged not worth the complexity; if that
judgement is wrong, it's a small addition to `load_credentials()` below.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import upstox_client

from src.config import resolve_upstox_token

CREDENTIALS_PATH = Path.home() / ".config" / "nifty-spot-intraday-collector" / "upstox_credentials.json"
AUTH_DIALOG_URL = "https://api.upstox.com/v2/login/authorization/dialog"
AUTH_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


def get_upstox_client(token: "str | None" = None) -> upstox_client.ApiClient:
    """Return an authenticated Upstox API client."""
    access_token = token or _load_token()
    configuration = upstox_client.Configuration()
    configuration.access_token = access_token
    return upstox_client.ApiClient(configuration)


def looks_like_upstox_token(token: str) -> bool:
    """Reject obvious placeholders ('paste-your-token-here', etc.) before
    they reach the API. Upstox access tokens are JWTs: three dot-separated
    base64 segments starting with 'eyJ' (the base64 of '{"')."""
    return token.startswith("eyJ") and token.count(".") >= 2 and len(token) > 100


def _load_token() -> str:
    token = resolve_upstox_token()
    if token:
        return token

    if CREDENTIALS_PATH.exists():
        with open(CREDENTIALS_PATH) as f:
            data = json.load(f)
        token = data.get("access_token")
        if token:
            return token

    raise ValueError(
        "Upstox access token not found. Run `nifty-spot auth` to obtain one, "
        "or set UPSTOX_ACCESS_TOKEN, or write it as "
        f'{{"access_token": "..."}} to {CREDENTIALS_PATH}. '
        "See docs/CREDENTIALS.md."
    )


def save_token(token: str) -> None:
    """Write the token to the credential file, 0600, atomically."""
    token = (token or "").strip()
    if not token:
        raise ValueError("Token cannot be empty")
    if not looks_like_upstox_token(token):
        raise ValueError(
            "Token does not look like a valid Upstox token (expected a JWT "
            "starting with 'eyJ'). Paste the full token, not a placeholder."
        )
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(CREDENTIALS_PATH.parent, 0o700)
    tmp = CREDENTIALS_PATH.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump({"access_token": token}, f, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(CREDENTIALS_PATH)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def load_credentials() -> dict:
    """The Upstox APP registration values -- from registering an app at
    https://developer.upstox.com, not from logging in. Env-var only; see
    this module's docstring for why there's no file fallback."""
    return {
        "client_id": os.environ.get("UPSTOX_CLIENT_ID"),
        "client_secret": os.environ.get("UPSTOX_CLIENT_SECRET"),
        "redirect_uri": os.environ.get("UPSTOX_REDIRECT_URI"),
    }


def get_login_url(redirect_uri: "str | None" = None, state: "str | None" = None) -> str:
    """The URL to open in a browser to start the login. Upstox will redirect
    back to `redirect_uri` with a one-time `code` query parameter after the
    user logs in by hand -- that redirect is the manual step nothing here
    scripts around."""
    creds = load_credentials()
    client_id = creds["client_id"]
    final_redirect = redirect_uri or creds["redirect_uri"]
    if not client_id:
        raise ValueError("UPSTOX_CLIENT_ID is not set. See docs/CREDENTIALS.md.")
    if not final_redirect:
        raise ValueError("UPSTOX_REDIRECT_URI is not set (or pass --redirect-uri). See docs/CREDENTIALS.md.")

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": final_redirect,
    }
    if state:
        params["state"] = state
    return f"{AUTH_DIALOG_URL}?{urlencode(params)}"


def exchange_auth_code(code: str, redirect_uri: "str | None" = None) -> dict:
    """Trade the one-time authorization code for an access token. This is
    the one function in this repo that makes an unprompted, non-market-data
    HTTP POST -- to Upstox's own token endpoint, and only when a human just
    ran `nifty-spot auth` and pasted a code. Does NOT save the token; the
    caller (src/cli.py's `auth` command) does that via save_token(), so the
    JWT-shape check in save_token() runs on every token this repo persists,
    regardless of how it arrived."""
    creds = load_credentials()
    client_id = creds["client_id"]
    client_secret = creds["client_secret"]
    final_redirect = redirect_uri or creds["redirect_uri"]
    if not client_id or not client_secret:
        raise ValueError(
            "UPSTOX_CLIENT_ID and UPSTOX_CLIENT_SECRET must both be set. See docs/CREDENTIALS.md."
        )
    if not final_redirect:
        raise ValueError("UPSTOX_REDIRECT_URI is not set (or pass --redirect-uri). See docs/CREDENTIALS.md.")

    payload = urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": final_redirect,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request = Request(
        AUTH_TOKEN_URL,
        data=payload,
        headers={
            "accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            # Without an explicit User-Agent, urllib sends its own default
            # ("Python-urllib/3.x") -- one of the most commonly blocklisted
            # bot signatures on the internet, and confirmed live on
            # 2026-08-09 to trigger Cloudflare's "browser_signature_banned"
            # rule on api.upstox.com (HTTP 403, error_code 1010): "Your
            # user-agent has been banned by the site owner." This value
            # isn't a guess -- it's upstox-python-sdk's own default
            # User-Agent (upstox_client.ApiClient().user_agent), confirmed
            # working against this exact domain by every successful
            # src/upstox/adapter.py historical-candle request, which goes
            # through that SDK and was never blocked.
            "User-Agent": "Swagger-Codegen/1.0.0/python",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        # Upstox's token endpoint returns a JSON error body on rejection
        # (the same shape as the login-URL's UDAPI100068 response) -- read
        # it rather than letting HTTPError propagate as a bare "HTTP Error
        # 403: Forbidden" traceback. Authorization codes are single-use and
        # short-lived, so the most likely cause is pasting one that's
        # already been used or has expired; a wrong UPSTOX_CLIENT_SECRET is
        # the other likely cause, since this is the first call in the flow
        # that uses it (get_login_url() only needs client_id).
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Upstox rejected the token exchange (HTTP {exc.code}): {body} "
            "-- authorization codes are single-use and expire quickly, so "
            "run `nifty-spot auth` again for a fresh one if you waited "
            "before pasting it back. If that keeps failing, double-check "
            "UPSTOX_CLIENT_SECRET against the Upstox dashboard."
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Upstox's token endpoint: {exc.reason}") from exc
