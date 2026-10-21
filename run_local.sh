#!/usr/bin/env bash
# MissionPilot AI - run on macOS / Linux
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
export SECRET_KEY="${SECRET_KEY:-local-dev-secret-change-me}"
echo "Open http://localhost:8000"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
