@echo off
cd /d "%~dp0"
python setup_assistant.py
if errorlevel 1 (
  echo.
  echo Python n'est pas installe ou pas dans le PATH.
  echo Installe-le depuis https://www.python.org/downloads/ en cochant "Add python.exe to PATH".
  pause
)
