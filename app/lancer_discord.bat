@echo off
cd /d "%~dp0"
title Bot Discord MHA
where python >nul 2>nul
if errorlevel 1 (
  echo Python est introuvable. Installe-le depuis https://www.python.org/downloads/ en cochant "Add python.exe to PATH".
  pause
  exit /b 1
)
python -c "import discord" 2>nul
if errorlevel 1 (
  echo Installation de discord.py ...
  python -m pip install discord.py
)
if not exist discord_config.json (
  python configurer_discord.py
  if not exist discord_config.json (
    echo Configuration annulee.
    pause
    exit /b 1
  )
)
:boucle
python discord_bot.py
if errorlevel 2 (
  echo.
  echo Erreur de configuration ^(voir ci-dessus^). Corrige discord_config.json puis relance.
  pause
  exit /b 2
)
echo.
echo Le bot s'est arrete. Relance dans 30 secondes (ferme cette fenetre pour quitter)...
timeout /t 30 >nul
goto boucle
