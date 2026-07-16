@echo off
REM Double-click this file to launch the LED sign GUI.
REM Works no matter where the folder is located, since it switches to its
REM own directory first.

cd /d "%~dp0"
python sign_gui.py

if errorlevel 1 (
    echo.
    echo Something went wrong ^(see the error above^).
    pause
)
