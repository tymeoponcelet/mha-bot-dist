@echo off
cd /d "%~dp0"
rem Modifie la ligne ci-dessous pour changer l'enchainement (voir enchainer.py)
python enchainer.py supply:money:4:10 supply:gear:2:3 nightops:5
pause
