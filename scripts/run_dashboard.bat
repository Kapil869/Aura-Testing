@echo off
cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
echo Dashboard: http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
