<#
One-click setup on a new PC: everything Monster Hunter Tri x FINAL FANTASY XI needs, installed and built, then
the launcher started. Double-click "Setup.bat" in the project folder, or from PowerShell:
    powershell -ExecutionPolicy Bypass -File tools\setup.ps1 [-Disc <image>] [-NoPlay]

  1. wiikit, the Wii toolkit (a git submodule: GitHub's "Download ZIP" leaves it out, so it is fetched)
  2. FINAL FANTASY XI looked for (your own installation; its installer's page opened if it isn't there)
  3. uv and its Pythons (runs the launcher and the build's tools)
  4. MSYS2's compiler and libraries (clang, CMake, Ninja, SDL3)
  5. The game, built from your own disc image (tools\build-game.ps1)
  6. The launcher started (Play FFXI-MH3.bat)

-Disc: the disc image (.rvz, .iso, .wbfs or .wia), passed on to build-game.ps1; else it asks.
-NoPlay: the launcher isn't started at the end.
Each step is skipped once done, so after fixing a problem run it again: it carries on where it stopped.
#>
param([string]$Disc, [switch]$NoPlay)

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Msys = "C:\msys64"
$ProgressPreference = "SilentlyContinue"         # Windows PowerShell's progress bar slows downloads to a crawl
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Say($text, $colour = "Gray") { Write-Host $text -ForegroundColor $colour }
function Step($n, $what) { Say ""; Say "[$n/6] $what" Cyan }
function Fail($what, $fix) {
    Say ""
    Say "STOPPED: $what" Red
    foreach ($line in $fix) { Say "  $line" Yellow }
    Say ""
    Say "Fix that, then run Setup again: it carries on from this step."
    exit 1
}
# what was just installed is on the PATH Windows keeps, not yet on this window's
function Update-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:PATH = "$machine;$user;$env:USERPROFILE\.local\bin"
}
function Have($exe) { [bool](Get-Command $exe -ErrorAction SilentlyContinue) }

Say "Setting up Monster Hunter Tri x FINAL FANTASY XI in $Root"
if (-not [Environment]::Is64BitOperatingSystem) { Fail "This is a 32-bit Windows." @("The game needs 64-bit Windows 10 or 11.") }

# ---- 1. wiikit -----------------------------------------------------------------------------------
Step 1 "wiikit, the Wii toolkit"
if (Test-Path "wiikit\recomp") {
    Say "  Here."
} else {
    if ((Test-Path ".git") -and (Have git)) {
        Say "  Fetching it with git..."
        & git submodule update --init --recursive
    }
    if (-not (Test-Path "wiikit\recomp")) {
        # downloaded as a zip: wiikit's own zip, from the address and branch .gitmodules names
        $mods = Get-Content ".gitmodules" -Raw -ErrorAction SilentlyContinue
        $url = [regex]::Match("$mods", 'url\s*=\s*(\S+)').Groups[1].Value -replace '\.git$', ''
        $branch = [regex]::Match("$mods", 'branch\s*=\s*(\S+)').Groups[1].Value
        if (-not $url) { Fail ".gitmodules is missing, so wiikit's address isn't known." @("Download the whole project again.") }
        if (-not $branch) { $branch = "main" }
        Say "  Downloading it from $url ($branch)..."
        $zip = Join-Path $env:TEMP "wiikit-$branch.zip"
        $unzip = Join-Path $env:TEMP "wiikit-unzip"
        try {
            Invoke-WebRequest "$url/archive/refs/heads/$branch.zip" -OutFile $zip -UseBasicParsing
            Remove-Item $unzip -Recurse -Force -ErrorAction SilentlyContinue
            Expand-Archive $zip $unzip -Force
            $inner = Get-ChildItem $unzip -Directory | Select-Object -First 1
            New-Item -ItemType Directory -Force "wiikit" | Out-Null
            Copy-Item (Join-Path $inner.FullName "*") "wiikit" -Recurse -Force
        } catch {
            Fail "wiikit couldn't be downloaded ($($_.Exception.Message))." @(
                "Check the internet connection, or download $url yourself (Code, Download ZIP)",
                "and extract it into the project's wiikit folder.")
        } finally {
            Remove-Item $zip, $unzip -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not (Test-Path "wiikit\recomp")) { Fail "wiikit's folder is still empty." @("Download the whole project again.") }
    Say "  Here now."
}

# ---- 2. FINAL FANTASY XI -------------------------------------------------------------------------
Step 2 "FINAL FANTASY XI (your own installation)"
$ffxi = $null
foreach ($key in "PlayOnlineUS", "PlayOnlineEU", "PlayOnline") {
    $dir = (Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\$key\InstallFolder" -ErrorAction SilentlyContinue)."0001"
    if ($dir -and (Test-Path (Join-Path $dir "ROM"))) { $ffxi = $dir; break }
}
if (-not $ffxi) {
    foreach ($dir in "${env:ProgramFiles(x86)}\PlayOnline\SquareEnix\FINAL FANTASY XI", "$env:ProgramFiles\PlayOnline\SquareEnix\FINAL FANTASY XI") {
        if (Test-Path (Join-Path $dir "ROM")) { $ffxi = $dir; break }
    }
}
if ($ffxi) {
    Say "  Found: $ffxi"
} else {
    Say "  Not found. Install it with Square Enix's official installer, from the page that opens:" Yellow
    Say "    https://www.playonline.com/ff11us/" Yellow
    Say "  Its files are all the game reads (it never connects to FFXI's servers). Setup goes on meanwhile;" Yellow
    Say "  if FFXI goes somewhere else, the launcher asks for its folder." Yellow
    Start-Process "https://www.playonline.com/ff11us/"
}

# ---- 3. uv ---------------------------------------------------------------------------------------
Step 3 "uv (runs the launcher and the build's tools)"
Update-Path
if (-not (Have uv)) {
    Say "  Installing uv (astral.sh/uv)..."
    & powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    Update-Path
    if (-not (Have uv)) {
        Fail "uv didn't install." @("SETUP-INSTRUCTIONS.txt, STEP 2, by hand:",
            '  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"')
    }
}
& uv python install 3.12 3.14
if ($LASTEXITCODE -ne 0) { Fail "uv couldn't install Python." @("The messages above say why; check the internet connection.") }
Say "  uv and Python are here."

# ---- 4. MSYS2 ------------------------------------------------------------------------------------
Step 4 "MSYS2's compiler and libraries (clang, CMake, Ninja, SDL3)"
$bash = "$Msys\usr\bin\bash.exe"
if (-not (Test-Path $bash)) {
    if (Have winget) {
        Say "  Installing MSYS2 with winget (Windows may ask to allow it)..."
        & winget install --id MSYS2.MSYS2 -e --accept-package-agreements --accept-source-agreements
    }
    if (-not (Test-Path $bash)) {
        Say "  Installing MSYS2 with msys2.org's installer (Windows may ask to allow it)..."
        $exe = Join-Path $env:TEMP "msys2-x86_64-latest.exe"
        try {
            Invoke-WebRequest "https://github.com/msys2/msys2-installer/releases/latest/download/msys2-x86_64-latest.exe" -OutFile $exe -UseBasicParsing
            Start-Process $exe -ArgumentList "in", "--confirm-command", "--accept-messages", "--root", "C:/msys64" -Wait
        } catch {
            Say "  ($($_.Exception.Message))" Yellow
        } finally {
            Remove-Item $exe -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not (Test-Path $bash)) {
        Fail "MSYS2 didn't install." @("Install it from https://www.msys2.org/ (to C:\msys64, the usual place), then run Setup again.")
    }
}
$tools = @("clang++.exe", "cmake.exe", "ninja.exe", "SDL3.dll")
$missing = @($tools | Where-Object { -not (Test-Path "$Msys\mingw64\bin\$_") })
if ($missing) {
    Say ("  Installing " + ($missing -join ", ") + " (MSYS2 updates itself first; a few minutes)...")
    & $bash -lc "pacman -Syu --noconfirm"
    & $bash -lc "pacman -Syu --noconfirm"         # again: the first may update only MSYS2's own core
    & $bash -lc "pacman -S --needed --noconfirm mingw-w64-x86_64-clang mingw-w64-x86_64-cmake mingw-w64-x86_64-ninja mingw-w64-x86_64-sdl3"
    $missing = @($tools | Where-Object { -not (Test-Path "$Msys\mingw64\bin\$_") })
    if ($missing) {
        Fail ("MSYS2's tools are still missing: " + ($missing -join ", ") + ".") @(
            "SETUP-INSTRUCTIONS.txt, STEP 3: run its last two commands by hand, then Setup again.")
    }
}
Say "  MSYS2's tools are here."

# ---- 5. the game ---------------------------------------------------------------------------------
Step 5 "The game, built from your own Monster Hunter Tri disc image (the first time, 15 to 40 minutes)"
$build = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "build-game.ps1"))
if ($Disc) { $build += @("-Disc", $Disc) }
& powershell @build
if ($LASTEXITCODE -ne 0) {
    Say ""
    Say "Setup stopped at the game's build: fix what it says above, then run Setup again." Red
    exit 1
}

# ---- 6. play -------------------------------------------------------------------------------------
Step 6 "Ready"
Say "  Next time, just double-click ""Play FFXI-MH3.bat""." Green
if (-not $ffxi) { Say "  Install FINAL FANTASY XI before you play (step 2 above)." Yellow }
if (-not $NoPlay) {
    Say "  Starting the launcher: point it at FFXI, extract its interface (once), make your character, Play." Green
    Start-Process (Join-Path $Root "Play FFXI-MH3.bat") -WorkingDirectory $Root
}
exit 0
