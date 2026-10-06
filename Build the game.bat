@echo off
rem Builds the game (build\recomp-build\wiiboot.exe) from your own Monster Hunter Tri disc image:
rem tools\build-game.ps1, SETUP-INSTRUCTIONS.txt's step 5 in one go. Run it again after fixing anything it stops on.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build-game.ps1" %*
echo.
pause
