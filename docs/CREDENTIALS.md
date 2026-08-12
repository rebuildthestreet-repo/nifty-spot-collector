# Connecting your broker account

To collect data you need two things from your broker: a set of **app keys**
(you get these once) and a **daily access token** (you refresh this each
morning).

This page walks you through getting both, then documents every setting in
detail if you need it.

> **About brokers.** This is a NIFTY 50 spot data collector, not an Upstox
> tool — the broker is a replaceable part. **Upstox is the only broker wired
> up in this build**, so every concrete instruction here names Upstox and
> its screens. Others (Zerodha, Dhan, Fyers, paid data plans) could be
> added later; broker-specific code is deliberately confined to one place
> (`src/upstox/`) so that stays possible. The setting names below start with
> `UPSTOX_` for the same reason they name a specific broker: they're what
> today's code actually reads.

> ⚠️ **Partly verified.** This page was first written by someone who had
> never opened the broker's developer site. On 2026-08-09 a real user with a
> real account worked through it live and confirmed several parts — those
> say **CONFIRMED 2026-08-09**. Anything still marked `[SCREENSHOT: ...]` or
> `[VERIFY: ...]` has **not** been checked against the real site: mainly the
> login-redirect flow and registering an app from scratch, since the
> confirming session used an app that already existed. Everything describing
> *this project's own code* is verified against the code itself.

---

## The two kinds of credential

These are easy to confuse, and confusing them causes most of the trouble.

| | What it is | How often |
|---|---|---|
| **App keys** | An API Key, an API Secret, and a Redirect URL. Identify *your app* to the broker. | Once, when you register an app. Rarely change. |
| **Access token** | Proves *you* are logged in right now. | **Expires every morning.** You refresh it daily. |

You set up the app keys once. After that, the only recurring task is the
daily token, which takes about 30 seconds.

---

## Step 1 — Register an app with your broker

You need a developer app on your broker's site to get API keys.

*Steps below are for Upstox, the only broker supported today.*

1. Sign in at [developer.upstox.com](https://developer.upstox.com) with your
   broker account.
   `[SCREENSHOT: developer.upstox.com sign-in]`

2. Create a new app.
   `[SCREENSHOT: the "create a new app" flow — CONFIRMED that the resulting
   app appears under Apps → Algo Trading, alongside Analytics / Sandbox /
   Expired tabs; NOT confirmed what the creation form itself looks like or
   asks for]`

3. **Enter a Redirect URL.** What to put here depends on how you'll connect —
   see [Step 2](#step-2--choose-how-youll-connect) below and pick first, as
   the two need different values.

   `[VERIFY: still open — whether Upstox validates the URL is reachable at
   registration time, or accepts it unconditionally]`

4. **Find your keys afterwards.** **CONFIRMED 2026-08-09**: they are *not*
   on a confirmation page after creating the app. Go to **Apps** → the
   **Algo Trading** tab, find your app in the table, and click the chevron to
   expand its row. That reveals:

   - **API Key** — copy this into `UPSTOX_CLIENT_ID`
   - **API Secret** — masked, with a reveal/copy button. Copy into
     `UPSTOX_CLIENT_SECRET`
   - **Access Token** — a ready-made daily token; see
     [Step 3](#step-3--get-todays-access-token)

   `[SCREENSHOT: the completed app page]`

> **Heads up on naming.** Your broker's screens and this project's settings
> use different words for the same thing. Upstox says **"API Key"**; this
> project stores it as `UPSTOX_CLIENT_ID`. Same value. The launcher's
> Credentials form shows both labels side by side so you don't have to hold
> this in your head.

**Note**: the Redirect URL is set on a *different* page from where the keys
are shown — **Apps** → open the app → **Edit app**, a field labelled
**"Redirect URL"** with the helper text *"After successfully login in, the
authorization will redirect here."* (**CONFIRMED** location.)

---

## Step 2 — Choose how you'll connect

Two options, and they need **different** Redirect URLs. Pick one before
finishing Step 1.

### Using the launcher (recommended)

Register this exact Redirect URL:

```
http://127.0.0.1:8765/callback
```

The launcher is a real web server on your machine, so it catches the login
response itself — nothing to copy or paste. Plain `http` and `127.0.0.1` are
correct here; loopback addresses are exempt from needing HTTPS under the
usual OAuth convention for desktop apps.

If you run the launcher on a different port, this address must change to
match, in both places.

`[VERIFY: still open — whether Upstox's dashboard accepts a plain http://
redirect URL for a loopback address, as opposed to the
https://127.0.0.1/callback placeholder confirmed working below for the
manual-paste flow. Not yet tested against a real app registration.]`

### Using the command line

Register a placeholder — **it doesn't need to be a real website**:

```
https://127.0.0.1/callback
```

**CONFIRMED 2026-08-09**: this exact value, set identically in both the
dashboard and your settings file, was accepted.

Nothing listens at that address. After you log in, your browser lands on an
error page — **that's expected** — and the part you need is in the address
bar, which you paste back into the terminal.

> **It must match character for character** in both places, or the login is
> rejected outright.
>
> `[VERIFY: still open — the confirming session's failure turned out to be a
> wrong CLIENT_ID, not a wrong redirect_uri, so what error is returned for a
> genuinely MISMATCHED redirect_uri specifically remains unconfirmed.
> UDAPI100068's message names both fields without saying which is at fault.]`

---

## Step 3 — Get today's access token

**CONFIRMED 2026-08-09**: there are two working ways, and the second was a
surprise.

### Option A — log in through the tool

In the launcher, press **Connect**. From the terminal, run `nifty-spot auth`.

Either way you log in on your broker's site and a fresh token is saved for
you. The launcher captures it automatically; the CLI asks you to paste the
address bar back in.

### Option B — copy it from the dashboard

**CONFIRMED 2026-08-09**: the same expanded app row that shows your API Key
and Secret also shows an **Access Token**, marked "Generated By You", with a
copy button and a **Revoke** button — a live token without any login
redirect.

Paste it into the launcher's **Paste a token** box, or into
`UPSTOX_ACCESS_TOKEN` in your settings file. This was tested end to end:
`collect` stored 375/375 real bars for a real trading day using a token
obtained this way.

Option B is simpler where it's available — no redirect, no pasting codes, and
you don't need the API Secret or Redirect URL at all, since only the login
flow reads those. Whether every account type offers it isn't established.

> ⚠️ **The trap that follows from Option B — CONFIRMED 2026-08-09.** A token
> set in your settings file **always wins** over one saved by logging in. No
> staleness check, no warning. So if you use Option B once and later switch
> to Option A, that old pasted token keeps overriding every fresh one — and
> the symptom is "logging in worked, but collecting still fails," the next
> day, for a reason that has nothing to do with logging in.
>
> **Fix**: blank out `UPSTOX_ACCESS_TOKEN` in your settings file once you're
> relying on the login flow. `./verify.sh` reports *which source* your token
> came from, specifically so you can catch this.

---

## What each setting does

Five settings. There is no other config file, and nowhere else secrets can
live in this project.

| Setting | Needed for | Notes |
|---|---|---|
| `SPOT_DB_PATH` | Everything | Where your database file goes |
| `UPSTOX_ACCESS_TOKEN` | Collecting | The daily token. Usually left blank if you log in |
| `UPSTOX_CLIENT_ID` | Logging in | Your broker calls this **API Key** |
| `UPSTOX_CLIENT_SECRET` | Logging in | Your broker calls this **API Secret** |
| `UPSTOX_REDIRECT_URI` | Logging in | Must match your broker's setting exactly |

### `SPOT_DB_PATH`

The full path to your database file. It doesn't need to exist — it's created
on first collect.

**There is deliberately no default.** If this isn't set, every command
refuses to run and exits with status 2 rather than inventing a path. A
default here caused real damage in the system this project came from: it
silently created an empty database somewhere unexpected, and runs against it
reported "0 rows" while looking exactly like success.

*Docker note*: ignored. `docker-compose.yml` always sets it to
`/data/spot.db` inside the container.

*Read by*: `src/config.py::resolve_db_path()`, checked by every command
before it does anything.

### `UPSTOX_ACCESS_TOKEN`

The daily token. **Usually you leave this blank** and let the login flow save
it for you; it's read from a saved credentials file when this is empty. Set
it directly only if you're using Option B above or managing tokens yourself.

Format: a JWT — three dot-separated segments starting with `eyJ`, well over
100 characters. That shape is checked before the token is ever saved or used,
to catch a half-copied paste before it reaches the API.

If it's missing **and** nothing is saved: `collect` refuses to run and exits
with status 2 before making any API call. `chart` doesn't need it (it only
reads your local database), and the login flow is how you get one.

> **CONFIRMED 2026-08-09 — a genuinely surprising finding.** Upstox's
> historical-candle endpoint for NIFTY spot doesn't appear to require a
> valid token at all: direct `curl` tests with no auth header, an empty one,
> and an obviously fake token all returned real data (against a genuine TLS
> certificate, ruling out interception).
>
> This doesn't mean the setting is pointless — the broker could tighten this
> without notice, it may not hold for every account or region, and the shape
> check still catches paste mistakes. It does mean `verify.sh`'s live-API
> check ("Historical API reachable") **cannot confirm your token is valid**,
> only that the network path and instrument key work. It was renamed from
> "Token accepted by Upstox" for exactly that reason.

*Read by*: `src/upstox/session.py::_load_token()`.

### `UPSTOX_CLIENT_ID` — your broker's "API Key"

Identifies your registered app when logging in.

**Format — CONFIRMED 2026-08-09**: a UUID (8-4-4-4-12 hex digits, like
`f5f5f626-226f-41ad-8ee9-42dab7e53273`), not a short alphanumeric string. A
live test with a wrong, short value produced error `UDAPI100068` ("Check your
'client_id' and 'redirect_uri'; one or both are incorrect") — see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md).

If missing, logging in fails immediately with a message naming it, before
opening any URL. Collecting and charting never read it.

*Read by*: `src/upstox/session.py::load_credentials()`.

### `UPSTOX_CLIENT_SECRET` — your broker's "API Secret"

Proves the login response really came from your app. **Treat it as a
password**: anyone with your Key and Secret can impersonate your app.

Issued alongside the API Key, in the same expanded row, shown masked with a
reveal/copy button. (Its exact character format wasn't recorded during the
confirming session — only the Key was read off screen.)

If missing, logging in gets as far as showing you the login link, then fails
the moment you send a code back — before the token exchange.

### `UPSTOX_REDIRECT_URI`

Where your broker sends your browser after login, with a one-time code
attached. See [Step 2](#step-2--choose-how-youll-connect) for which value to
use — it differs between the launcher and the command line.

Can be overridden per-run with `nifty-spot auth --redirect-uri URL`. If
missing and not passed, logging in fails before opening anything.

---

## What `nifty-spot auth` actually does

The launcher's **Connect** button runs exactly this. Read from
`src/cli.py::_cmd_auth`:

1. Builds a login URL from your API Key and Redirect URL, and prints it. If
   either is unset it stops here with a named error.
2. You open that URL and log in.
   `[SCREENSHOT: the Upstox login page you land on after opening the URL]`
   `[SCREENSHOT: the Upstox permission/consent screen, if one exists, before
   the redirect]`
3. Your browser is redirected to your Redirect URL with `?code=...`
   attached. On the command line that address usually isn't a real server, so
   you'll see a "can't reach this page" error — **expected**; the code is in
   the address bar. The launcher, being a real server, skips this entirely.
   `[VERIFY: confirm what the browser actually shows at this step — a
   connection error page, a blank page, or something else, and whether the
   code is reliably visible/copyable in the address bar in that state]`
4. It prompts `Paste code or redirect URL:`. You can paste **either** the
   bare code **or** the whole address bar — it extracts the code itself.
   Pasting a URL with no code in it fails with a clear message rather than
   sending a malformed request.
5. It exchanges the code for a token — the one network call here that isn't
   the market-data API, and only after you've pasted something back.

   > **CONFIRMED 2026-08-09 — this step once failed for an unrelated
   > reason.** The request had no `User-Agent`, and `urllib`'s default
   > (`Python-urllib/3.x`) is widely blocked by Cloudflare bot management
   > (`error_code 1010`, `browser_signature_banned`) — nothing to do with
   > your credentials. Fixed by sending the broker SDK's own User-Agent,
   > already proven against that domain. Full error text in
   > [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

6. The token is shape-checked, then saved to
   `~/.config/nifty-spot-collector/upstox_credentials.json` with `0600`
   permissions, written atomically.
7. It confirms, and reminds you the token expires tomorrow morning.

Nothing here is scheduled or retried automatically, and step 4 needs a human.
That's deliberate — see [ARCHITECTURE.md](ARCHITECTURE.md).

In Docker, prefix with
`docker compose run --rm collector python -m src.cli auth`.
