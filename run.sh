#!/usr/bin/env bash
# Start Greenpack Inspector (web app) on Mac/Linux
set -e
cd "$(dirname "$0")"

python3 -c "import fastapi, uvicorn, cv2, skimage, reportlab" 2>/dev/null || {
  echo "Installing dependencies (first run)..."
  python3 -m pip install -r requirements.txt
}

echo "Starting Greenpack Inspector at http://127.0.0.1:8000"
( sleep 3 && (xdg-open http://127.0.0.1:8000 2>/dev/null || open http://127.0.0.1:8000 2>/dev/null) ) &
cd backend
python3 -m uvicorn server:app --host 127.0.0.1 --port 8000
