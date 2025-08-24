#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "Starting Teleprompter Web Interface..."
echo "Open http://localhost:5000"

PY="./teleprompter-venv/bin/python"
if [[ -x "$PY" ]]; then
  QT_QPA_PLATFORM=xcb "$PY" web_interface.py
else
  QT_QPA_PLATFORM=xcb python3 web_interface.py
fi
