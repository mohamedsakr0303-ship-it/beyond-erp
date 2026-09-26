#!/bin/bash
# Beyond ERP launcher for macOS. Double-click in Finder (first time: right-click > Open).
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.9+ is required. Install it from https://www.python.org/downloads/"
  read -r -p "Press Enter to close"; exit 1
fi
python3 -c "import openpyxl" 2>/dev/null || python3 -m pip install --user --quiet openpyxl
exec python3 run.py "$@"
