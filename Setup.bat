@echo off
rem One-click setup on a new PC (tools\setup.ps1): the toolkit, uv, MSYS2, the game built from your own
rem Monster Hunter Tri disc image, FINAL FANTASY XI looked for, then the launcher. Run it again after fixing
rem anything it stops on: each step is skipped once done.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup.ps1" %*
echo.
pause
