#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
command -v python3 >/dev/null 2>&1 || { echo "Install Python 3.11 or newer."; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
printf '\nOpen http://127.0.0.1:8765 ; press Ctrl+C to stop.\n'
exec .venv/bin/python -m qiming serve --port 8765
