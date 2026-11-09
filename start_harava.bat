@echo off
cd /d "%~dp0"
title Harava
where python >nul 2>nul || (echo Python was not found. Install Python 3.9+ from https://www.python.org/ and tick "Add python.exe to PATH". & pause & exit /b 1)
python -c "import flask" 2>nul || python -m pip install -r requirements.txt
python harava.py
pause
