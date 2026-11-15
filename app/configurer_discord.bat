@echo off
cd /d "%~dp0"
title Configurer le bot Discord MHA
where python >nul 2>nul
if errorlevel 1 (
  echo Python est introuvable. Installe-le depuis https://www.python.org/downloads/ en cochant "Add python.exe to PATH".
  pause
  exit /b 1
)
python configurer_discord.py
if not exist discord_config.json (
  echo Configuration annulee.
  pause
  exit /b 1
)
echo.
echo Configuration terminee : on met le bot EN LIGNE ^(laisse cette fenetre ouverte, tu dois voir "Connecte")...
call lancer_discord.bat
