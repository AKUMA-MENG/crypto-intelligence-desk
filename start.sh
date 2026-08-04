#!/usr/bin/env sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_URL="http://127.0.0.1:8899"

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  printf '%s\n' "Python 3.8+ was not found. Install Python from https://www.python.org/downloads/ and run this file again." >&2
  exit 1
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
  printf '%s\n' "Python 3.8 or newer is required." >&2
  exit 1
fi

(
  sleep 1
  if command -v open >/dev/null 2>&1; then
    open "$APP_URL"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$APP_URL"
  fi
) >/dev/null 2>&1 &

cd "$APP_DIR"
printf '%s\n' "Crypto Intelligence Desk is running at $APP_URL"
printf '%s\n' "Press Ctrl+C to stop."
exec "$PYTHON" proxy.py
