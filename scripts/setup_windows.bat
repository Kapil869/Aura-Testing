@echo off
REM One-time setup: creates .venv and installs dependencies.
cd /d "%~dp0\.."
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m venv .venv || goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || goto :error
echo.
echo Setup complete. Start the dashboard with: scripts\run_dashboard.bat
exit /b 0
:error
echo Setup failed. Make sure Python 3.11+ is installed and on PATH.
exit /b 1
