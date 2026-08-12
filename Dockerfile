# nifty-spot-collector -- the Docker route.
#
# Pinned base image, not `python:3.12-slim` or `:latest`: a floating tag can
# change what Debian release (and what package versions) you get on a
# rebuild months apart, silently. bookworm rather than the newer trixie that
# plain "3.12.13-slim" now resolves to, for the same reason -- an explicit
# Debian codename in the tag can't drift out from under a later rebuild the
# way an implicit default can.
FROM python:3.12.13-slim-bookworm

# tzdata, EXPLICITLY, not left to the pip `tzdata` package this image also
# installs via requirements.txt. ZoneInfo("Asia/Kolkata") -- used in five
# modules of this repo -- raises ZoneInfoNotFoundError at IMPORT time on a
# slim image with neither. That happens before argparse has even parsed a
# command, so none of this repo's own error handling ever runs; the failure
# is a raw Python traceback pointing at whatever module happened to import
# first. Installing the OS package here means the failure this repo is
# actually designed to catch (missing config, missing token, empty range)
# is the first thing that CAN go wrong, not tzdata silently taking that spot.
# sqlite3 is the CLI binary, for manually inspecting the database if you ever
# need to -- nothing in this repo's own code shells out to it; the standard
# library's sqlite3 module talks to the file directly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies before source, so an image rebuild after a code-only change
# reuses this layer instead of reinstalling everything.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY verify.sh .
RUN chmod +x verify.sh

# A dedicated, unprivileged user -- and a FIXED, EXPLICIT $HOME, which is the
# actual point. This repo's Python code resolves the credentials file via
# Path.home() (see src/upstox/session.py), extracted as-is from the source
# system, which assumes a writable Unix home. Left to Docker's own default
# that's /root for a container that never runs USER; here it's a real,
# owned, writable directory whose path this Dockerfile controls -- so the
# unmodified Python code resolves it correctly, rather than the container
# silently inheriting whatever the base image happened to default to.
# docker-compose.yml mounts a named volume at exactly this path so the token
# survives a container recreate.
RUN useradd --create-home --home-dir /home/collector --uid 1000 collector \
    && mkdir -p /data /home/collector/.config/nifty-spot-collector \
    && chown -R collector:collector /data /home/collector /app

USER collector
ENV HOME=/home/collector
ENV PYTHONUNBUFFERED=1

# No ENTRYPOINT pinning this to `python -m src.cli`: verify.sh and the CLI
# are both valid things to run, and forcing one as the entrypoint would make
# the other awkward to invoke through `docker compose run`. The default
# command below is verify.sh, so a bare `docker compose up` -- the one
# command the Docker route promises -- actually proves the container works instead of
# just proving argparse can print a usage error. See docker-compose.yml for
# the exact commands to run collect/chart/auth.
CMD ["./verify.sh"]
