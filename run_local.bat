@echo off
REM MissionPilot AI - run on your Windows laptop
cd /d %~dp0
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
if "%SECRET_KEY%"=="" set SECRET_KEY=local-dev-secret-change-me
echo.
echo Open http://localhost:8000 in your browser
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
