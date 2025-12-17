@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python AoS\remote_approver.py
pause
