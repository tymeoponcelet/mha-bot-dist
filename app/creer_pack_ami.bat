@echo off
cd /d "%~dp0"
python creer_pack_ami.py
if errorlevel 1 python3 creer_pack_ami.py
echo.
pause
