# nifty-spot-collector

**Collect intraday (minute-by-minute) price data for the NIFTY index and store it in a database on your own machine. For intraday analysis. View it on a chart with my-view-chart.**

**Build your own database of NIFTY 50 price history, and chart it.**

You pick a date range. This downloads NIFTY 50 minute-by-minute prices for
those days from your broker, saves them into a database file on your own
computer, and draws you a candlestick chart.

That is all it does. It doesn't trade, doesn't run in the background, and
doesn't do options, futures, or live prices. You start it when you want
data; it finishes and stops.

It's built for people who want their own market data to learn and
experiment with, without needing to be a programmer to get it.

> **Which broker?** This collects NIFTY 50 data — the broker is just where
> it fetches from, and is a replaceable part. **Upstox is the only one
> wired up in this build**, so the setup steps name Upstox. Others
> (Zerodha, Dhan, Fyers, paid data plans) could be added later; all
> broker-specific code sits in one place to keep that door open.

> **Educational software.** Not investment advice, no SEBI registration, no
> security recommended. Ships no market data of its own. Full text:
> [DISCLAIMER.md](DISCLAIMER.md).

---

## What you'll need first

| | |
|---|---|
| **A broker account** | Free to open. You'll also register a small "app" on their developer site to get your keys — about 10 minutes, walked through in [docs/CREDENTIALS.md](docs/CREDENTIALS.md). **Upstox is the only broker supported right now** — see the note below. |
| **About 350 MB of disk space** | More if you choose Docker later — see [Running it in Docker instead](#running-it-in-docker-instead). |
| **20–30 minutes** | For the first-time setup. After that, starting it takes seconds. |

---

## Step 1 — Install the tools

Find your system below. You only need to do this once.

### Windows

Windows doesn't come with any of the tools this needs, so one script
installs them all for you.

1. Click **Start**, type `PowerShell`, then **right-click** *Windows
   PowerShell* and choose **Run as administrator**.

2. Copy all three lines below, paste them into that window, and press Enter:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
   irm https://raw.githubusercontent.com/rebuildthestreet-repo/nifty-spot-collector/main/bootstrap-windows.ps1 -OutFile bootstrap-windows.ps1
   .\bootstrap-windows.ps1
   ```

   This installs Git, Python, SQLite, and Docker. It takes about 10 minutes
   and prints what it's doing as it goes.

3. **Restart your computer** when it finishes. This matters — some of what
   it installs isn't active until you do.

4. After restarting, click **Start**, type `Git Bash`, and open it. Use
   **this** window for everything below — not PowerShell, not Command
   Prompt. They can't run the commands in Step 2.

> **Don't want Docker?** It's 2.3 GB and you don't need it. Use
> `.\bootstrap-windows.ps1 -SkipDocker` in step 2 instead.
>
> **Want to read the script before running it?** Sensible — it installs
> software as administrator. Run `notepad bootstrap-windows.ps1` after the
> download line, before the last line.

### macOS

Open **Terminal** (press Cmd+Space, type `Terminal`, press Enter).

You probably already have what's needed. If something's missing, the setup
in Step 3 tells you exactly what to install and gives you the command to do
it — so just carry on to Step 2.

### Linux

Open your terminal.

Same as macOS: carry on to Step 2, and the setup in Step 3 will tell you
if anything's missing along with the exact command for your distribution.

---

## Step 2 — Download this project

In your terminal (**Git Bash** on Windows), run:

```bash
git clone https://github.com/rebuildthestreet-repo/nifty-spot-collector.git
cd nifty-spot-collector
```

---

## Step 3 — Start it

```bash
python3 launcher/server.py
```

On Windows, type `python` instead of `python3`.

A page opens in your browser. That page is the tool — you do everything
from there, and it walks you through it in order:

1. **Status** — shows what's ready and what isn't.
2. **Install dependencies** — one button. Sets up everything the tool needs
   to run, inside this folder only.
3. **Credentials** — a form for your broker keys. Each box is labelled with
   the same words your broker uses on their own website, and tells you where to
   find that value.
4. **Connect** — links your broker account. Click, log in, done.
5. **Collect** — pick your dates, press the button, and **your chart
   appears on the page.**

Nothing is hidden: every command it runs is printed on screen before it
runs, so you can see exactly what's happening — and do it yourself from a
terminal next time if you'd rather.

> *"command not found: python3"*? Python isn't installed yet. On macOS,
> `brew install python` (or install [Homebrew](https://brew.sh) first). On
> Linux, use your package manager. On Windows, re-run Step 1.

**That's it.** You now have a database of real market data on your machine
and a chart of it. Full walkthrough of every screen:
[docs/LAUNCHER.md](docs/LAUNCHER.md).

---

## Coming back the next day

**Your broker access expires every morning.** This is your broker's rule, not a
bug here, and there's no way around it — you reconnect once a day.

Start the tool, look at the top of the page. If it says your token has
expired, click **Connect** and log in again. About 30 seconds. Everything
you've already collected stays in your database.

---

## Things worth knowing

Not urgent, but they'll save you confusion later.

**"It said 0 rows" is not the same as "it worked."** Run `./verify.sh` any
time you want a straight answer about what's actually set up and how many
rows you really have. It's written specifically to tell "correctly set up
but genuinely empty" apart from "never found your settings at all".

**Volume is always zero, and that's correct.** NIFTY 50 is an index, not
something people trade directly, so there's no volume figure to report.

**A full trading day is 375 rows.** 9:15 to 15:29. If you see fewer, it's
usually a holiday or a half-day, not a fault.

**Times are Indian market time (IST).** Stored as plain text like
`2026-06-01 09:15:00`, with no timezone marker attached. If you write your
own code against this database, don't treat those as UTC.

**Collecting a big range takes a while.** It fetches one day per request,
with a small pause between them, so a 60-day range means 60 requests. If it
stops partway, just run it again — days already saved aren't re-saved, and
nothing gets duplicated.

**The holiday calendar only covers 2026.** For other years it assumes
weekdays are trading days, which will be wrong on holidays. It warns you
when this happens.

Hitting something odd? [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
is organised by exactly what you're seeing on screen.

---

## Prefer the command line?

Everything above is also three plain commands. The browser page never
replaces them — it just runs these for you.

Install once, then edit `.env` with your broker keys and database location
(copy `.env.example` to start; it explains every field):

```bash
./setup.sh
cp .env.example .env
```

Then, whenever you want data:

```bash
.venv/bin/python -m src.cli auth                                              # once a day
.venv/bin/python -m src.cli collect --from 2026-06-01 --to 2026-06-05
.venv/bin/python -m src.cli chart  --from 2026-06-01 --to 2026-06-05 --out chart.svg
./verify.sh                                                                   # check anything
```

On Windows use `.venv/Scripts/python` instead of `.venv/bin/python`, and
run it from **Git Bash**.

You don't need to `source .env` — it's read automatically. Anything you've
already set in your shell wins over the file.

---

## Running it in Docker instead

Optional, and heavier: Docker Desktop is ~2.26 GB by itself, and the image
adds more on top — roughly 2.6 GB against 350 MB for a normal install, for
the same few hundred lines of Python. Worth it only if you specifically
want everything sealed off from the rest of your machine.

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/),
**open it and leave it running**, then:

```bash
cp .env.example .env        # then edit it with your broker keys
docker compose build
docker compose run --rm collector python -m src.cli auth
docker compose run --rm collector python -m src.cli collect --from 2026-06-01 --to 2026-06-05

# To get the chart out of the container and onto your computer:
docker compose run --rm -v "$(pwd):/host" collector \
  python -m src.cli chart --from 2026-06-01 --to 2026-06-05 --out /host/chart.svg

docker compose up           # checks everything works
```

The launcher is not available on this route — Docker is command-line only.

---

## What's actually been confirmed working

This project says what it has and hasn't tested, rather than implying
everything is proven.

| | Status |
|---|---|
| Normal install on **macOS** | **Confirmed end-to-end**, real Upstox account, real data (2026-08-09) |
| Docker on **macOS** | **Confirmed end-to-end** (2026-08-09) |
| **Windows** | Prerequisite installs confirmed on real hardware. The full run — start to chart — is **not yet confirmed**; you may be the first. |
| Docker on **Windows / Linux** | **Not confirmed** |
| Upstox portal screens in [docs/CREDENTIALS.md](docs/CREDENTIALS.md) | Partly confirmed against the real dashboard; the rest is marked in that file |

---

## More

- [docs/LAUNCHER.md](docs/LAUNCHER.md) — every screen of the browser page,
  and what it does and doesn't do.
- [docs/CREDENTIALS.md](docs/CREDENTIALS.md) — getting your broker keys,
  and every setting explained.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — organised by symptom.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the code is put
  together, if you want to extend it.
- [AGENTS.md](AGENTS.md) — for a coding agent working on this repo.
