#!/usr/bin/env bash
# nifty-spot-intraday-collector -- native install.
#
# Detects prerequisites and instructs; NEVER installs a system package
# itself. If something's missing, this script prints the exact command for
# your OS and exits non-zero -- it does not run apt/brew/dnf/pacman for you.
# The one thing it WILL do is create a Python virtualenv (.venv) and install
# this repo's own pinned dependencies into it, because that's contained to
# this directory and can't touch anything else on your system.
#
# Usage: ./setup.sh
set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIN_PY_MAJOR=3
MIN_PY_MINOR=11  # matplotlib 3.11.1 (pinned in requirements.txt) requires >=3.11

missing=0

# ---------------------------------------------------------------------------
# Which interpreter is "python3" here?
#
# `python3` is the norm on macOS and Linux, but NOT on Windows: the
# python.org installer -- which is exactly what `winget install
# Python.Python.3.12` lays down, and what bootstrap-windows.ps1 installs --
# provides `python.exe` and the `py` launcher, and does not create a
# `python3` at all. This script used to hardcode `python3`, so on a freshly
# bootstrapped Windows box it reported "python3 not found" while a perfectly
# good Python 3.12 sat on PATH under a different name.
#
# Resolved once here, then used everywhere below as "$PY".
# ---------------------------------------------------------------------------
PY=""
for _cand in python3 python; do
    if command -v "$_cand" >/dev/null 2>&1; then
        if "$_cand" -c 'import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
            PY="$_cand"
            break
        fi
    fi
done

# ---------------------------------------------------------------------------
# A virtualenv's executables live in bin/ on macOS/Linux and Scripts/ on
# Windows. Same `python -m venv` command, different layout -- so any path
# built as "$VENV_DIR/bin/pip" is simply wrong on Windows. Echoes the
# correct directory for whichever layout actually exists.
# ---------------------------------------------------------------------------
venv_bin_dir() {
    if [ -d "$1/Scripts" ]; then
        printf '%s\n' "$1/Scripts"
    else
        printf '%s\n' "$1/bin"
    fi
}

echo "nifty-spot-intraday-collector setup"
echo "==========================="
echo

# ---------------------------------------------------------------------------
# OS / package-manager detection, for the install commands below only.
# ---------------------------------------------------------------------------
OS="$(uname -s 2>/dev/null || echo unknown)"
PKG_MGR="unknown"
case "$OS" in
    Darwin)
        PKG_MGR="brew"
        ;;
    Linux)
        if command -v apt-get >/dev/null 2>&1; then PKG_MGR="apt"
        elif command -v dnf >/dev/null 2>&1; then PKG_MGR="dnf"
        elif command -v pacman >/dev/null 2>&1; then PKG_MGR="pacman"
        elif command -v apk >/dev/null 2>&1; then PKG_MGR="apk"
        fi
        ;;
    MINGW*|MSYS*|CYGWIN*)
        # Git Bash (Git for Windows) or MSYS2 or Cygwin -- the only kinds
        # of shell that can run this script on Windows at all, since
        # PowerShell/cmd.exe can't execute a #!/usr/bin/env bash script.
        # If we're running, *some* bash-capable environment exists; that
        # says nothing about whether python3/pip/sqlite3 do -- Windows
        # ships none of them by default.
        PKG_MGR="windows"
        ;;
esac

# Windows has no single package-manager one-liner the way brew/apt/dnf do --
# each prerequisite's actual install story is genuinely different -- so it
# gets its own dispatch by name rather than a positional package name.
#
# winget package IDs below are CONFIRMED, not guessed: checked directly
# against microsoft/winget-pkgs' public manifests on 2026-08-10
# (Python.Python.3.12, Git.Git, SQLite.SQLite -- the last one was a real
# surprise, since this file used to say "no single installer exists" for
# sqlite3. It does: SQLite.SQLite's manifest installs sqlite3.exe from the
# official sqlite.org tools zip via winget's own "portable" installer type,
# with a PortableCommandAlias -- no manual PATH edit needed.
#
# Both the winget line and the manual-download fallback are ALWAYS shown
# together, unconditionally -- an earlier version of this function tried to
# detect winget first (`command -v winget`) and only show the winget line
# if that succeeded. Dropped 2026-08-10: a real user had a working winget
# (confirmed from PowerShell) that this script's `command -v winget` still
# reported as absent -- winget.exe is commonly exposed via an "App
# Execution Alias" stub in %LOCALAPPDATA%\Microsoft\WindowsApps\, and
# Git Bash's PATH resolution doesn't reliably see into that mechanism.
# Gating a genuinely useful command behind a check that unreliable defeats
# the point of offering it -- showing both costs a few extra lines, not a
# false negative on the option that actually would have worked.
windows_hint() {
    case "$1" in
        python3*)
            echo "    If you have winget (Windows 11, or an updated Windows 10 --"
            echo "    try it even if this script can't confirm it, Git Bash doesn't"
            echo "    always see it):"
            echo "      winget install Python.Python.3.12"
            echo "    Otherwise, download and run the official installer:"
            echo "      https://www.python.org/downloads/windows/"
            echo "    On its first screen, check \"Add python.exe to PATH\" --"
            echo "    pip is bundled and installed automatically alongside it."
            ;;
        pip)
            echo "    Bundled with Python either way (winget's Python.Python.3.12 or"
            echo "    the official installer above) -- if python3 is present but pip"
            echo "    isn't, reinstall python3 rather than trying to add pip alone."
            ;;
        git)
            echo "    If you have winget, try it even if this script can't confirm it:"
            echo "      winget install Git.Git"
            echo "    Otherwise, download and run \"Git for Windows\":"
            echo "      https://git-scm.com/download/win"
            echo "    This is also what provides Git Bash -- the shell this script"
            echo "    itself needs to run at all. If you're reading this message,"
            echo "    some bash environment already exists, but double-check you're"
            echo "    in \"Git Bash\" specifically (Start menu) and re-run after"
            echo "    installing, not the shell you're in now."
            ;;
        sqlite3)
            echo "    If you have winget, try it even if this script can't confirm it:"
            echo "      winget install SQLite.SQLite"
            echo "    Otherwise, download the command-line tools zip yourself:"
            echo "      https://www.sqlite.org/download.html"
            echo "    (the \"sqlite-tools-win...\" bundle) and add the extracted"
            echo "    folder to your PATH by hand."
            echo "    Nothing in this repo's own Python code actually runs the"
            echo "    sqlite3 CLI -- the standard library talks to the database"
            echo "    file directly -- so this is for your own convenience"
            echo "    inspecting the database by hand, not a functional requirement."
            ;;
    esac
}

install_hint() {
    # $1 = human name used only in the printed message (informational)
    case "$PKG_MGR" in
        brew)    echo "    brew install $2" ;;
        apt)     echo "    sudo apt-get update && sudo apt-get install -y $3" ;;
        dnf)     echo "    sudo dnf install -y $4" ;;
        pacman)  echo "    sudo pacman -S $5" ;;
        apk)     echo "    sudo apk add $6" ;;
        windows) windows_hint "$1" ;;
        *)
            echo "    Could not detect your package manager (OS: $OS)."
            echo "    Install $1 using whatever your system normally uses,"
            echo "    or use Docker instead -- see README.md."
            ;;
    esac
}

# ---------------------------------------------------------------------------
# python3, and its version
# ---------------------------------------------------------------------------
if [ -n "$PY" ]; then
    if "$PY" -c "import sys; sys.exit(0 if sys.version_info >= (${MIN_PY_MAJOR}, ${MIN_PY_MINOR}) else 1)"; then
        PY_VERSION="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
        echo "[OK]   python3 ${PY_VERSION} (>= ${MIN_PY_MAJOR}.${MIN_PY_MINOR} required, found as '${PY}')"
    else
        PY_VERSION="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
        echo "[FAIL] python3 ${PY_VERSION} found (as '${PY}'), but ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ is required"
        echo "  Install a newer python3:"
        install_hint "python3 (>=${MIN_PY_MAJOR}.${MIN_PY_MINOR})" "python@3.12" "python3.12 python3.12-venv python3-pip" "python3.12" "python" "python3"
        missing=1
    fi
else
    echo "[FAIL] python3 not found (looked for 'python3' and 'python' on PATH)"
    echo "  Install python3:"
    install_hint "python3" "python@3.12" "python3.12 python3.12-venv python3-pip" "python3.12" "python" "python3"
    missing=1
fi

# ---------------------------------------------------------------------------
# pip (via python3 -m pip, not the bare `pip` binary, which may not exist
# under that name even when pip itself is installed)
# ---------------------------------------------------------------------------
if [ -n "$PY" ] && "$PY" -m pip --version >/dev/null 2>&1; then
    echo "[OK]   pip ($("$PY" -m pip --version | cut -d' ' -f1,2))"
else
    echo "[FAIL] pip not found (${PY:-python3} -m pip failed)"
    echo "  Install pip:"
    install_hint "pip" "python@3.12" "python3-pip" "python3-pip" "python-pip" "py3-pip"
    missing=1
fi

# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------
if command -v git >/dev/null 2>&1; then
    echo "[OK]   git ($(git --version | cut -d' ' -f3))"
else
    echo "[FAIL] git not found"
    echo "  Install git:"
    install_hint "git" "git" "git" "git" "git" "git"
    missing=1
fi

# ---------------------------------------------------------------------------
# sqlite3 (the CLI binary -- nothing in this repo's own code shells out to
# it, the standard library's sqlite3 module talks to the file directly, but
# it's the tool you'll reach for to inspect the database by hand)
# ---------------------------------------------------------------------------
if command -v sqlite3 >/dev/null 2>&1; then
    echo "[OK]   sqlite3 ($(sqlite3 --version | cut -d' ' -f1))"
else
    echo "[FAIL] sqlite3 not found"
    echo "  Install sqlite3:"
    install_hint "sqlite3" "sqlite" "sqlite3" "sqlite" "sqlite" "sqlite"
    missing=1
fi

echo

if [ "$missing" -ne 0 ]; then
    echo "One or more prerequisites are missing (see [FAIL] above). Install"
    echo "them using the commands printed, then re-run ./setup.sh."
    echo
    echo "This script will not install system packages for you -- see this"
    echo "file's header comment for why."
    exit 1
fi

# ---------------------------------------------------------------------------
# All prerequisites present. Create/refresh the virtualenv and install this
# repo's own pinned dependencies into it -- contained to $REPO_DIR/.venv,
# touches nothing else on the system.
# ---------------------------------------------------------------------------
echo "All prerequisites present. Setting up the virtual environment..."
echo

VENV_DIR="${REPO_DIR}/.venv"
if [ ! -d "$VENV_DIR" ]; then
    "$PY" -m venv "$VENV_DIR"
    echo "[OK]   Created ${VENV_DIR}"
else
    echo "[OK]   ${VENV_DIR} already exists, reusing it"
fi

# bin/ on macOS/Linux, Scripts/ on Windows -- resolved after the venv exists,
# so this reads the real layout rather than guessing from the OS.
VENV_BIN="$(venv_bin_dir "$VENV_DIR")"
if [ ! -d "$VENV_BIN" ]; then
    echo "[FAIL] ${VENV_DIR} exists but has neither bin/ nor Scripts/ in it."
    echo "  The virtualenv looks incomplete. Delete it and re-run: rm -rf '${VENV_DIR}'"
    exit 1
fi

"${VENV_BIN}/pip" install --quiet --upgrade pip
"${VENV_BIN}/pip" install --quiet -r "${REPO_DIR}/requirements.txt"
echo "[OK]   Installed pinned dependencies from requirements.txt"

echo
echo "Setup complete. Next steps:"
echo "  1. cp .env.example .env   # then edit .env"
echo "  2. ${VENV_BIN}/python -m src.cli auth"
echo "  3. ${VENV_BIN}/python -m src.cli collect --from YYYY-MM-DD --to YYYY-MM-DD"
echo "  Run ./verify.sh at any point to check everything is wired up correctly."
echo
echo "  Or skip the terminal entirely -- run the browser launcher instead:"
echo "    ${VENV_BIN}/python launcher/server.py"
