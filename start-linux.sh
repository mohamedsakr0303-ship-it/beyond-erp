#!/bin/sh
# Beyond ERP launcher for Linux.
cd "$(dirname "$0")" || exit 1
command -v python3 >/dev/null 2>&1 || { echo "Python 3.9+ is required (e.g. sudo apt install python3)"; exit 1; }
python3 -c "import openpyxl" 2>/dev/null || python3 -m pip install --user --quiet openpyxl 2>/dev/null \
  || echo "Note: Excel import/export needs openpyxl (sudo apt install python3-openpyxl)"
exec python3 run.py "$@"
