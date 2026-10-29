@echo off
cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
python -m ruff check . && python -m pytest --html=reports\local-report.html --self-contained-html
