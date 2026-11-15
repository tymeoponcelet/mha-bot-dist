@echo off
cd /d "%~dp0"
echo Capture de l ecran de l emulateur (enregistree dans le dossier debug)...
python auto_aa.py --capture
echo.
pause
