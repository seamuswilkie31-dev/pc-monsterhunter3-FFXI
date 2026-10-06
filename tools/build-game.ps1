<#
Builds the game on this PC from your own Monster Hunter Tri disc image: SETUP-INSTRUCTIONS.txt's step 5
in one go. The game (build\recomp-build\wiiboot.exe) is Monster Hunter Tri's own code, translated and
compiled here, so it can't be downloaded: every PC makes its own from its own disc.

Double-click "Build the game.bat" in the project folder, or from PowerShell:
    powershell -ExecutionPolicy Bypass -File tools\build-game.ps1 [-Disc <image>] [-Dolphin <folder>]

-Disc: the disc image (.rvz, .iso, .wbfs or .wia); else the one in the build folder, else it asks.
-Dolphin: Dolphin's folder (the one holding Sys); else it looks in the usual places, else it asks.
Each step is skipped once done, so after fixing a problem run it again: it carries on where it stopped.
#>
param([string]$Disc, [string]$Dolphin)

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Msys = "C:\msys64\mingw64\bin"
$GameId = "RMHP08"                      # Monster Hunter Tri, Europe: the only version this port knows

function Say($text, $colour = "Gray") { Write-Host $text -ForegroundColor $colour }
function Step($n, $what) { Say ""; Say "[$n/7] $what" Cyan }
function Fail($what, $fix) {
    Say ""
    Say "STOPPED: $what" Red
    foreach ($line in $fix) { Say "  $line" Yellow }
    Say ""
    Say "Fix that, then run this again: it carries on from this step."
    exit 1
}
function Run($why, [string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) {
        Fail "$why failed (the messages above say why)." @("If they don't make sense, send a screenshot of this window.")
    }
}

Say "Building Monster Hunter Tri x FINAL FANTASY XI in $Root"

# ---- 1. the tools -------------------------------------------------------------------------------
Step 1 "Checking the tools (uv, MSYS2's compiler)"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Fail "uv isn't installed (or PowerShell was opened before it was)." @(
        "SETUP-INSTRUCTIONS.txt, STEP 2:",
        '  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
        "  uv python install 3.12 3.14",
        "then close this window and run it again.")
}
$missing = @("clang++.exe", "cmake.exe", "ninja.exe", "SDL3.dll") | Where-Object { -not (Test-Path (Join-Path $Msys $_)) }
if ($missing) {
    Fail ("MSYS2's tools are missing: " + ($missing -join ", ") + ".") @(
        "SETUP-INSTRUCTIONS.txt, STEP 3:",
        "  winget install --id MSYS2.MSYS2 -e",
        '  C:\msys64\usr\bin\bash.exe -lc "pacman -Syu --noconfirm"',
        '  C:\msys64\usr\bin\bash.exe -lc "pacman -S --needed --noconfirm mingw-w64-x86_64-clang mingw-w64-x86_64-cmake mingw-w64-x86_64-ninja mingw-w64-x86_64-sdl3"')
}
Say "  uv and MSYS2 are here."
New-Item -ItemType Directory -Force build, build\fonts | Out-Null

# ---- 2. the disc's files ------------------------------------------------------------------------
Step 2 "Monster Hunter Tri's files, from your disc image"
$dol = "build\extract\sys\main.dol"
if ((Test-Path $dol) -and (Test-Path "build\extract\files\01")) {
    Say "  Already unpacked (build\extract)."
} else {
    if (-not $Disc) {
        $found = @(Get-ChildItem build -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in ".rvz", ".iso", ".wbfs", ".wia" })
        if ($found.Count -eq 1) { $Disc = $found[0].FullName }
    }
    if (-not $Disc) {
        Say "  Choose your Monster Hunter Tri disc image in the window that opens."
        Add-Type -AssemblyName System.Windows.Forms
        $pick = New-Object System.Windows.Forms.OpenFileDialog
        $pick.Title = "Your Monster Hunter Tri (Europe) disc image"
        $pick.Filter = "Wii disc images (*.rvz;*.iso;*.wbfs;*.wia)|*.rvz;*.iso;*.wbfs;*.wia|All files (*.*)|*.*"
        if ($pick.ShowDialog() -eq "OK") { $Disc = $pick.FileName }
    }
    if (-not $Disc -or -not (Test-Path $Disc)) {
        Fail "No disc image." @("Copy your Monster Hunter Tri disc image (.rvz or .iso) into the project's build folder,",
                                "or choose it in the window this opens.")
    }
    Say "  Unpacking $Disc (a few minutes)..."
    Run "Unpacking the disc" uv @("run", "-q", "--python", "3.14", "python", "-m", "wiikit.disc", $Disc, "--extract", "build\extract")
    if (-not (Test-Path $dol)) { Fail "The disc unpacked, but build\extract\sys\main.dol isn't there." @("Is it a Wii disc image?") }
}
$id = [System.Text.Encoding]::ASCII.GetString([System.IO.File]::ReadAllBytes((Resolve-Path "build\extract\sys\boot.bin")), 0, 6)
if ($id -ne $GameId) {
    Fail "This disc is $id, but the port needs Monster Hunter Tri's European release, $GameId." @(
        "(The American release is RMHE08, the Japanese RMHJ08: their code is laid out differently.)",
        "Delete the build\extract folder, and use an image of the European disc.")
}
Say "  $id, the European release: good."

# ---- 3. Dolphin's files: the Wii's fonts, and the names of the SDK's functions ------------------
Step 3 "Dolphin's files (the Wii's fonts, and its function names)"
function Find-Dolphin {
    $places = @("$env:ProgramFiles\Dolphin*", "${env:ProgramFiles(x86)}\Dolphin*", "$env:LOCALAPPDATA\Programs\Dolphin*",
                "$env:USERPROFILE\Downloads\Dolphin*", "$env:USERPROFILE\Desktop\Dolphin*", "$env:USERPROFILE\Documents\Dolphin*",
                "$env:USERPROFILE\Dolphin*")
    foreach ($p in $places) {
        foreach ($d in @(Get-ChildItem $p -Directory -ErrorAction SilentlyContinue)) {
            $hit = Get-ChildItem $d.FullName -Filter totaldb.dsy -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($hit) { return $hit.Directory.Parent.FullName }
        }
    }
    foreach ($d in @(Get-ChildItem $env:USERPROFILE -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne "AppData" })) {
        $hit = Get-ChildItem $d.FullName -Filter totaldb.dsy -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $hit.Directory.Parent.FullName }
    }
    return $null
}
$fonts = @("font_western.bin", "font_japanese.bin")
$needFonts = @($fonts | Where-Object { -not (Test-Path "build\fonts\$_") })
$needNames = -not (Test-Path "build\sig_guess.tsv")
if ($needFonts -or $needNames) {
    if (-not $Dolphin) { $Dolphin = Find-Dolphin }
    if (-not $Dolphin) {
        Say "  Dolphin wasn't found in the usual places: choose its folder (the one holding Sys) in the window that opens."
        Add-Type -AssemblyName System.Windows.Forms
        $pick = New-Object System.Windows.Forms.FolderBrowserDialog
        $pick.Description = "Dolphin's folder (the one holding the Sys folder)"
        if ($pick.ShowDialog() -eq "OK") { $Dolphin = $pick.SelectedPath }
    }
    if ($Dolphin -and (Test-Path (Join-Path $Dolphin "Sys")) -eq $false -and (Split-Path -Leaf $Dolphin) -eq "Sys") { $Dolphin = Split-Path -Parent $Dolphin }
    $sys = if ($Dolphin) { Join-Path $Dolphin "Sys" } else { "" }
    if (-not $Dolphin -or -not (Test-Path (Join-Path $sys "totaldb.dsy"))) {
        Fail "Dolphin's Sys folder (with totaldb.dsy) wasn't found." @(
            "Install Dolphin (https://dolphin-emu.org/download/; the zip can just be extracted anywhere),",
            "then run this again, or name its folder:  -Dolphin ""C:\path\to\Dolphin-x64""")
    }
    Say "  Dolphin: $Dolphin"
    foreach ($f in @($fonts + "dsp_coef.bin")) {
        $src = Join-Path $sys "GC\$f"
        if ((Test-Path $src) -and -not (Test-Path "build\fonts\$f")) { Copy-Item $src "build\fonts\$f" }
    }
    $still = @($fonts | Where-Object { -not (Test-Path "build\fonts\$_") })
    if ($still) { Fail ("Dolphin's Sys\GC folder doesn't have " + ($still -join ", ") + ".") @("Try a newer Dolphin.") }
    Say "  The fonts are in build\fonts."
} else {
    Say "  Already done (build\fonts, build\sig_guess.tsv)."
}

# ---- 4. the game's functions named --------------------------------------------------------------
Step 4 "Naming the game's functions"
if (Test-Path "build\sig_guess.tsv") {
    Say "  Already matched (build\sig_guess.tsv)."
} else {
    Run "Matching the SDK's functions" uv @("run", "-q", "python", "tools\sigmatch.py", $dol, "--dsy", (Join-Path $sys "totaldb.dsy"), "--out", "build\sig_guess.tsv")
}
Run "Naming the functions" uv @("run", "-q", "python", "tools\names.py", $dol)

# ---- 5. translated into C++ ---------------------------------------------------------------------
Step 5 "Translating the game's code (a few minutes)"
$rso = @()
foreach ($f in Get-ChildItem "build\extract\files\01\*.rso") { $rso += "--rso"; $rso += $f.FullName }
Run "Translating the code" uv (@("run", "-q", "python", "-m", "wiikit.recomp", $dol, "--out", "build\recomp", "--symbols", "build\names.tsv",
                                 "--hooks", "tools\mh3-hooks.txt", "--sel", "build\extract\files\mh3.sel") + $rso)

# ---- 6. compiled ---------------------------------------------------------------------------------
Step 6 "Compiling the game (the longest step: 10 to 30 minutes, the PC busy throughout)"
$env:PATH = "$Msys;$env:PATH"
$extra = (Join-Path $Root "tools\mh3.cmake").Replace("\", "/")
if (-not (Test-Path "build\recomp-build\build.ninja")) {
    Run "Setting up the build" cmake @("-S", "build\recomp", "-B", "build\recomp-build", "-G", "Ninja", "-DCMAKE_CXX_COMPILER=clang++",
                                       "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_CXX_FLAGS_RELEASE=-O1", "-DWIIKIT_EXTRA=$extra")
}
if (Get-Process wiiboot -ErrorAction SilentlyContinue) {
    Fail "The game is running, so it can't be replaced." @("Close it, then run this again.")
}
Run "Compiling" ninja @("-C", "build\recomp-build")

# ---- 7. done -------------------------------------------------------------------------------------
Step 7 "Done?"
if (-not (Test-Path "build\recomp-build\wiiboot.exe")) { Fail "The build finished, but wiiboot.exe isn't there." @("Send a screenshot of this window.") }
New-Item -ItemType Directory -Force build\run1 | Out-Null
Say "  The game is built: build\recomp-build\wiiboot.exe" Green
Say ""
Say "Now double-click ""Play FFXI-MH3.bat"" (STEP 7 of SETUP-INSTRUCTIONS.txt)." Green
exit 0
