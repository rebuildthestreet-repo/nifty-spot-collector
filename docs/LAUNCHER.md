# The launcher

The launcher is a page that opens in your browser and walks you through
setting this up: checking what's installed, saving your broker keys,
connecting your account, and collecting data — ending with your chart on
screen.

It doesn't do anything you couldn't do by typing commands yourself. It runs
exactly those commands, shows you each one before it runs it, and prints the
output live. Everything it saves is a plain file you can open and edit. The
point is to remove the fiddly parts, not to hide what's happening.

> **About brokers.** This is a NIFTY 50 spot data collector, not an
> Upstox tool — the broker is a replaceable part of it. **Upstox is the only
> broker wired up in this build**, so the concrete instructions below name
> Upstox. Others (Zerodha, Dhan, Fyers, paid data plans) could be added
> later; all broker-specific code is kept in one place so that stays
> possible.

---

## Start it

```bash
python3 launcher/server.py
```

On Windows, type `python` instead of `python3`.

Your browser opens at `http://127.0.0.1:8765/`. Press **Ctrl+C** in the
terminal to stop it.

All it needs is Python 3.11 or newer. It installs nothing to run itself — a
tool whose job is helping you install things can't require you to install
things first.

**On Windows, run `bootstrap-windows.ps1` as Administrator before this.**
The launcher can't bootstrap Python for you, because it needs Python to run
at all — and its Install button uses Git Bash, which that script also
installs. See the README's Windows section.

---

## The five things on the page

### Status (always visible)

What's ready and what isn't: your Python version, whether dependencies are
installed, whether your settings file was found and where, your database
location, and how many rows it holds.

This is `./verify.sh` — the same checks, shown as tiles instead of terminal
text. The raw output is underneath if you want it.

Across the top is your **access token** status. Brokers expire these daily,
so most mornings it will say expired. **That's normal, and it isn't shown as
an error** — just a note and a link to reconnect.

### 1. Install dependencies

One button. Runs `./setup.sh`, which builds an isolated environment inside
this folder and installs what the tool needs. Nothing is installed outside
this directory.

The command appears on screen before it runs, and its output streams live —
this takes a few minutes, so you can watch it work.

### 2. Credentials

A form that writes your settings file (`.env`).

Each box is labelled with **the words your broker uses on their own
website**, not this project's internal names — because those differ, and
that mismatch is the single thing that confused testers most. What Upstox
calls "API Key", for instance, this project stores as `UPSTOX_CLIENT_ID`.
Each field tells you both, plus where to find the value.

Saving keeps every comment already in your settings file and changes only
the lines you edited. Afterwards it shows you the file's full path and
contents, with secrets hidden behind a **Show** toggle.

> **If a field warns that a different value is in effect**, you have that
> setting exported in your terminal, and that always beats the file. The
> warning tells you which value is actually being used — without it, you'd
> edit a field, see nothing change, and have no idea why.

### 3. Connect

Two ways to get today's access token. Both are fine; pick whichever suits
you.

**Connect with your broker** — click the login link, log in on your broker's
site, and you're done. The page captures the response automatically.

This is the part that most justifies the launcher existing. Normally you'd
log in, get redirected to a dead address, and have to copy a code out of
your browser's address bar into a terminal. The launcher is already a web
server, so it just *receives* that redirect itself.

For this to work, register this exact address as your app's Redirect URL on
your broker's site:

```
http://127.0.0.1:8765/callback
```

**Paste a token** — Upstox's dashboard also shows a ready-made access token
you can copy. Paste it into this box and you're done, no login flow at all.

If your broker offers this, it's often the faster daily habit — one paste
versus a login. It's a first-class option here, not a fallback.

### 4. Collect

Pick a start and end date, press the button.

You'll see the exact command, then its output as it runs — one line per
trading day. When it finishes, **your chart appears on the page.** The file
path and a download link are there too, but you don't have to go looking for
anything.

If the collection fails — an empty date range, an expired token, a network
problem — you get the real error message, and no chart is drawn. It won't
pretend something worked.

---

## Changing the port

The launcher uses port `8765`. To change it:

```bash
python3 launcher/server.py --port 9000
```

**If you do this, you must also update the Redirect URL registered with your
broker**, and your settings file, to match the new port exactly. That
address has the port in it, and your broker checks it character for
character.

If port 8765 is already busy, the launcher stops and tells you so, rather
than quietly moving to another port — a silently different port would break
Connect in a way that's very hard to work out.

---

## Is it safe to run?

**It's only reachable from your own computer.** It binds to `127.0.0.1`,
never to your network address. Nobody else on your wifi can reach it.

**Other websites can't touch it.** Every request must be addressed to
`127.0.0.1` directly, and anything that changes your settings must come from
the launcher's own page. A page open in another tab attempting either gets
refused with an explanation. (Specifically: the `Host` header must match, and
state-changing requests must carry a matching `Origin` or none — this blocks
DNS rebinding and cross-origin `fetch()` respectively.)

**Secrets are hidden by default**, everywhere they're shown, with a per-field
**Show** toggle. That covers your API secret and access token — and your API
Key too, which isn't really secret, but uniform hiding is easier to trust
when you're screen-sharing than a rule about which fields are which.

**Nothing sensitive reaches the logs.** The launcher strips the query string
from everything it logs, so the one-time login code never lands in your
terminal history.

**It runs commands, it doesn't reimplement them.** The launcher shells out to
the same `nifty-spot` commands you'd type. It never imports the project's
internal code, which is why anything it can do, you can also do by hand.

**The chart view accepts no file paths from the browser.** It serves only the
one file the launcher itself just generated.

---

## Recording or screen-sharing this

One thing masking cannot cover: the **login link contains your API Key in
plain text**, because it's part of the address your broker's login page
reads (`?client_id=...`). Once you click it, it's in your browser's address
bar too.

Every field and file view in the launcher hides it. That URL can't. If your
API Key shouldn't be on camera, don't linger on the login link or the page
it opens.

---

## What it deliberately doesn't do

No indicators, no open-interest analysis, no option chains, no order
placement. No login or accounts on the launcher itself — it's for one person,
on one computer. No remote access. Nothing runs in the background: close the
terminal and it's gone, like any other command.

And it doesn't automate the daily token. That's still you, once a day, on
purpose. The launcher makes it a click or a paste instead of copying codes
between windows — it doesn't try to remove the step.
