#!/usr/bin/env sh
# Start Kliprix on macOS/Linux: ./scripts/start.sh  (stops with Ctrl+C)
# Any Python 3 can run the launcher; it picks Python 3.11-3.12 for the backend itself.
set -e
cd "$(dirname "$0")/.."
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    exec "$candidate" scripts/run.py "$@"
  fi
done
echo "[kliprix] ERROR: Python 3 was not found. Install Python 3.12 from https://www.python.org/downloads/" >&2
exit 1
