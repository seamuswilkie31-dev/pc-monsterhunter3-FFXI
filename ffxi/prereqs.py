# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "pillow>=10", "pynput>=1.7", "sounddevice>=0.4", "pycaw>=20240210"]
# ///
"""What this PC needs to play, checked: the launcher asks at start, and if anything is missing writes
SETUP-NEEDED.txt in the repository naming only that, each with the step of SETUP-INSTRUCTIONS.txt that
puts it right.

    uv run prereqs.py           # the check alone, its report printed
"""
from __future__ import annotations

import importlib
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MSYS = Path(r"C:\msys64\mingw64\bin")
PORT_DLLS = ("SDL3.dll", "libstdc++-6.dll", "libgcc_s_seh-1.dll", "libwinpthread-1.dll")
FONTS = ("font_western.bin", "font_japanese.bin")
PACKAGES = ("numpy", "PIL", "sounddevice", "pycaw", "pynput")


@dataclass
class Missing:
    what: str          # what's missing, in a few words
    why: str           # what it's for
    fix: str           # the step that puts it right, and its commands


def check(ffxi: str | None = None) -> list[Missing]:
    out: list[Missing] = []                                    # (Git isn't needed to play: only to download)
    if not shutil.which("uv"):
        out.append(Missing("uv", "runs the launcher and installs its Python parts",
                           'STEP 2:  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'))
    exe = ROOT / "build" / "recomp-build" / "wiiboot.exe"
    missing_dll = [d for d in PORT_DLLS if not (MSYS / d).exists() and not (exe.parent / d).exists()]
    if missing_dll:
        out.append(Missing("MSYS2's libraries (" + ", ".join(missing_dll) + ")", "the game's runtime needs them",
                           "STEP 3:  winget install --id MSYS2.MSYS2 -e, then the two pacman commands there"))
    extract = ROOT / "build" / "extract"
    if not (extract / "sys" / "main.dol").exists() or not (extract / "files" / "01").is_dir():
        out.append(Missing("Monster Hunter Tri's files (build\\extract)", "the game itself, from your own disc image",
                           "STEP 5a and the first command of 5c (wiikit.disc ... --extract build\\extract)"))
    fonts = [f for f in FONTS if not (ROOT / "build" / "fonts" / f).exists()]
    if fonts:
        out.append(Missing("the Wii's boot fonts (build\\fonts\\" + ", ".join(fonts) + ")", "the game's text",
                           "STEP 5b: copy them from Dolphin's Sys\\GC folder"))
    if not (ROOT / "build" / "recomp" / "symbols.tsv").exists():
        out.append(Missing("the recompiled game (build\\recomp)", "the game's code, made from your disc",
                           "STEP 5c: the commands from tools\\sigmatch.py to wiikit.recomp"))
    if not exe.exists():
        out.append(Missing("the built game (build\\recomp-build\\wiiboot.exe)", "the program that runs the game",
                           "STEP 5c: the cmake and ninja commands at its end"))
    root = Path(ffxi) if ffxi else None
    if not root or not (root / "ROM").is_dir():
        out.append(Missing("FINAL FANTASY XI (its folder holding ROM)", "FFXI's characters, music, menus and effects",
                           "STEP 6: install it, then point the launcher at its folder"))
    gone = []
    for p in PACKAGES:
        try:
            importlib.import_module(p)
        except Exception:                                              # noqa: BLE001
            gone.append(p)
    if gone:
        out.append(Missing("Python packages (" + ", ".join(gone) + ")", "the FFXI layer's sound, pictures and keys",
                           'Start through "Play FFXI-MH3.bat" (uv installs them); or STEP 2 again'))
    return out


def report(missing: list[Missing]) -> str:
    lines = ["=" * 78, "  WHAT THIS PC STILL NEEDS  (the launcher's check)", "=" * 78, "",
             "Each item below is missing. Fix them in this order, following the step named",
             "(SETUP-INSTRUCTIONS.txt, in this folder, has every step in full). Then start",
             '"Play FFXI-MH3.bat" again: this file is written anew each time, and removed',
             "once nothing is missing.", ""]
    for i, m in enumerate(missing, 1):
        lines += [f"{i}. {m.what}", f"     what it's for:  {m.why}", f"     how to fix:     {m.fix}", ""]
    return "\n".join(lines)


def write_report(missing: list[Missing]) -> Path | None:
    """SETUP-NEEDED.txt with what's missing (removed when nothing is): its path, or None."""
    p = ROOT / "SETUP-NEEDED.txt"
    if not missing:
        p.unlink(missing_ok=True)
        return None
    p.write_text(report(missing), encoding="utf-8")
    return p


def main() -> int:
    import json
    import os
    settings = Path(os.environ.get("LOCALAPPDATA", "")) / "ffxi-mh3" / "assets" / "launcher.json"
    try:
        ffxi = json.loads(settings.read_text(encoding="utf-8")).get("ffxi")
    except (OSError, ValueError):
        ffxi = None
    m = check(ffxi)
    print(report(m) if m else "Nothing missing: this PC can play.")
    return 1 if m else 0


if __name__ == "__main__":
    raise SystemExit(main())
