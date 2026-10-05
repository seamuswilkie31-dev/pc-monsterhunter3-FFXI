@echo off
rem Monster Hunter Tri x FINAL FANTASY XI: the launcher (ffxi/launcher.py). Needs uv (astral.sh/uv);
rem without it, the setup steps open (SETUP-INSTRUCTIONS.txt). The launcher checks the rest itself.
where uv >nul 2>nul
if errorlevel 1 (
  echo.
  echo  uv isn't installed, so the launcher can't start.
  echo  The setup steps are opening: follow STEP 2 ^(and the steps before it if this PC is new^).
  echo.
  start "" notepad "%~dp0SETUP-INSTRUCTIONS.txt"
  pause
  exit /b 1
)
cd /d "%~dp0ffxi"
uv run launcher.py
