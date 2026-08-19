<#
.SYNOPSIS
    nifty-spot-intraday-collector -- one-shot Windows prerequisite installer.

.DESCRIPTION
    Installs everything this repo needs on a Windows machine where NOTHING
    exists yet -- including winget itself, which is the part that made the
    old setup.sh-first approach painful. Run this ONCE, from an
    Administrator PowerShell, before cloning the repo.

    Why this exists, and why it's PowerShell rather than more setup.sh:
    setup.sh can only ever detect and instruct on Windows -- it can't
    install a system package, and it can't run at all until Git Bash
    exists, which is itself one of the things that needs installing. That's
    a chicken-and-egg problem no bash script can solve on a bare Windows
    box. The launcher has the same bootstrapping floor: it needs Python
    before it can help you install Python. This script is the one step that
    has to come first; after it, every other path in this repo (the
    launcher, setup.sh, the CLI directly) works the same way it does on
    macOS and Linux.

    What it installs:
      - winget itself (repaired/bootstrapped via the official
        Microsoft.WinGet.Client PowerShell module -- this is the part that
        works on machines where `winget` isn't present or is broken)
      - WSL core, no distribution (Docker Desktop's backend)
      - Git         -- to clone this repo, and for Git Bash, the shell
                       setup.sh and verify.sh need
      - Python 3.12 -- the only hard runtime requirement
      - SQLite CLI  -- to inspect the database by hand (optional in
                       practice; nothing in this repo shells out to it)
      - DB Browser for SQLite -- a GUI for the same
      - Docker Desktop -- ONLY needed for the optional Docker route; skip
                       it with -SkipDocker (see NOTES)

.PARAMETER SkipDocker
    Don't install Docker Desktop. Worth using: Docker is ~2.26GB and is
    only needed for the Docker route. The normal install is this repo's
    recommended one and doesn't touch Docker at all.

.PARAMETER SkipWsl
    Don't enable WSL. Only meaningful together with -SkipDocker, since
    WSL is Docker Desktop's backend.

.NOTES
    Run from an ADMINISTRATOR PowerShell:
      Start menu -> type "PowerShell" -> right-click -> Run as administrator

    Then:
      Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
      .\bootstrap-windows.ps1

    A REBOOT is required afterwards if WSL or Docker Desktop were
    installed. PATH changes need a NEW terminal window regardless -- the
    window you run this in will not see the newly-installed commands.
#>
[CmdletBinding()]
param(
    [switch]$SkipDocker,
    [switch]$SkipWsl
)

$progressPreference = 'silentlyContinue'
$script:Results = @()

function Write-Section($text) {
    Write-Host ""
    Write-Host "=== $text ===" -ForegroundColor Cyan
}

function Add-Result($name, $ok, $detail) {
    $script:Results += [pscustomobject]@{ Name = $name; OK = $ok; Detail = $detail }
}

# ---------------------------------------------------------------------------
# Administrator check. Everything below needs it -- winget's own repair path
# writes to machine scope, and `wsl --install` enables Windows features.
# Checked explicitly rather than via `#Requires -RunAsAdministrator` so the
# message names the fix instead of just refusing.
# ---------------------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "This script must run as Administrator." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Close this window. Open the Start menu, type 'PowerShell',"
    Write-Host "  right-click 'Windows PowerShell', and choose"
    Write-Host "  'Run as administrator'. Then run this script again."
    exit 1
}

Write-Host "nifty-spot-intraday-collector -- Windows prerequisite installer"
Write-Host "======================================================"

# ---------------------------------------------------------------------------
# STEP 1: bootstrap/repair winget itself.
#
# This is the part that makes the whole approach work on a bare machine.
# `winget` may be absent, or present-but-broken, or exposed only as an App
# Execution Alias that other tooling can't reliably see (the exact failure
# that made setup.sh's `command -v winget` detection untrustworthy from Git
# Bash -- see setup.sh's own header comment). Rather than detect any of
# that, install the official Microsoft.WinGet.Client module from PSGallery
# and let its Repair-WinGetPackageManager put a known-good winget in place.
# ---------------------------------------------------------------------------
Write-Section "STEP 1: Bootstrapping winget"

try {
    Write-Host "Installing NuGet package provider (PSGallery's backend)..."
    Install-PackageProvider -Name NuGet -Force | Out-Null

    Write-Host "Installing Microsoft.WinGet.Client from PSGallery..."
    Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery -Scope CurrentUser | Out-Null

    Write-Host "Repairing / installing the winget engine (this can take a few minutes)..."
    Repair-WinGetPackageManager -Force -Latest

    Write-Host "Refreshing the winget source index..."
    winget source update

    Add-Result "winget bootstrap" $true "Microsoft.WinGet.Client + Repair-WinGetPackageManager"
}
catch {
    Add-Result "winget bootstrap" $false $_.Exception.Message
    Write-Host ""
    Write-Host "winget could not be bootstrapped: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Nothing below can run without it. Stopping here." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# STEP 2: WSL core (Docker Desktop's backend), no distribution.
# ---------------------------------------------------------------------------
if (-not $SkipWsl -and -not $SkipDocker) {
    Write-Section "STEP 2: Enabling WSL (Docker Desktop's backend)"
    wsl --install --no-distribution
    if ($LASTEXITCODE -eq 0) {
        Add-Result "WSL core" $true "enabled, no distribution (a reboot is required)"
    }
    else {
        Add-Result "WSL core" $false "wsl --install exited $LASTEXITCODE"
    }
}
else {
    Add-Result "WSL core" $null "skipped"
}

# ---------------------------------------------------------------------------
# STEP 3: the applications.
#
# Package IDs are exact (-e), so winget can't silently resolve a search term
# to something else. Each install's exit code is checked individually and
# recorded -- one failure does NOT abort the batch, because a missing
# DB Browser shouldn't stop Python from installing.
#
# winget exit codes worth knowing: 0 is success, and
# 0x8A15002B (-1978335189) is "no applicable upgrade / already installed",
# which is a success for our purposes -- re-running this script is safe.
# ---------------------------------------------------------------------------
function Install-Pkg {
    param(
        [Parameter(Mandatory)] [string]$Id,
        [Parameter(Mandatory)] [string]$Label,
        [string]$Why
    )
    Write-Host ""
    Write-Host "-> $Label ($Id)"
    if ($Why) { Write-Host "   $Why" -ForegroundColor DarkGray }

    winget install --id $Id -e `
        --accept-package-agreements --accept-source-agreements --silent

    $code = $LASTEXITCODE
    # -1978335189 / 0x8A15002B: already installed, nothing to do.
    if ($code -eq 0 -or $code -eq -1978335189) {
        $detail = if ($code -eq 0) { "installed" } else { "already present" }
        Add-Result $Label $true $detail
        Write-Host "   [OK] $detail" -ForegroundColor Green
    }
    else {
        Add-Result $Label $false "winget exited $code"
        Write-Host "   [FAIL] winget exited $code" -ForegroundColor Red
    }
}

Write-Section "STEP 3: Installing applications"

Install-Pkg -Id "Git.Git" -Label "Git" `
    -Why "Needed to clone this repo, and provides Git Bash -- the shell setup.sh and verify.sh run in."

Install-Pkg -Id "Python.Python.3.12" -Label "Python 3.12" `
    -Why "The only hard runtime requirement. The launcher needs this before it can do anything."

Install-Pkg -Id "SQLite.SQLite" -Label "SQLite CLI" `
    -Why "For inspecting the database by hand. Nothing in this repo's own code shells out to it."

Install-Pkg -Id "DBBrowserForSQLite.DBBrowserForSQLite" -Label "DB Browser for SQLite" `
    -Why "A GUI for the same database. Entirely optional."

if (-not $SkipDocker) {
    Install-Pkg -Id "Docker.DockerDesktop" -Label "Docker Desktop" `
        -Why "Only needed for the Docker route (~2.26GB). Re-run with -SkipDocker to leave it out."
}
else {
    Add-Result "Docker Desktop" $null "skipped (-SkipDocker)"
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Section "Summary"

foreach ($r in $script:Results) {
    $tag = if ($r.OK -eq $true) { "[OK]  " } elseif ($r.OK -eq $false) { "[FAIL]" } else { "[SKIP]" }
    $color = if ($r.OK -eq $true) { "Green" } elseif ($r.OK -eq $false) { "Red" } else { "DarkGray" }
    Write-Host ("{0} {1} -- {2}" -f $tag, $r.Name, $r.Detail) -ForegroundColor $color
}

$failed = @($script:Results | Where-Object { $_.OK -eq $false })

Write-Host ""
if ($failed.Count -gt 0) {
    Write-Host "$($failed.Count) item(s) failed above. Everything else installed." -ForegroundColor Yellow
    Write-Host "Re-running this script is safe -- already-installed packages are skipped."
}
else {
    Write-Host "All requested prerequisites are installed." -ForegroundColor Green
}

Write-Host ""
Write-Host "NEXT STEPS" -ForegroundColor Cyan
Write-Host "  1. REBOOT if WSL or Docker Desktop were installed above."
Write-Host "     (Windows needs it to finish enabling those features.)"
Write-Host ""
Write-Host "  2. Open a NEW terminal. This window's PATH is stale and will not"
Write-Host "     see git, python, or sqlite3 even though they are now installed."
Write-Host ""
Write-Host "  3. Clone the repo and start the launcher:"
Write-Host "       git clone https://github.com/rebuildthestreet-repo/nifty-spot-intraday-collector.git"
Write-Host "       cd nifty-spot-intraday-collector"
Write-Host "       python launcher\server.py"
Write-Host ""
Write-Host "     The launcher takes it from there -- dependencies, credentials,"
Write-Host "     connecting to Upstox, and collecting. See docs\LAUNCHER.md."
Write-Host ""
Write-Host "     Prefer the terminal? Open 'Git Bash' (not PowerShell) and run"
Write-Host "     ./setup.sh -- see README.md."
