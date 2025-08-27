#!/usr/bin/env bash
set -euo pipefail

# Resolve repository root (directory of this script)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer venv Python if available
PYEXE="$DIR/teleprompter-venv/bin/python"
if [[ ! -x "$PYEXE" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYEXE="python3"
  else
    PYEXE="python"
  fi
fi

echo "Launching presenter key inspector..."
echo "Press buttons on your presenter. Press 'q' to quit."
exec "$PYEXE" "$DIR/teleprompter.py" check_keys
