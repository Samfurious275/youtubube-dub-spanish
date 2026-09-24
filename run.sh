#!/bin/bash
# First run installs everything; after that it just runs dub.py with your arguments.
set -e
cd "$(dirname "$0")"

for tool in ffmpeg yt-dlp; do
  command -v "$tool" >/dev/null || brew install "$tool"
done

if [ ! -x .venv/bin/python ]; then
  PY="$(brew --prefix)/opt/python@3.11/bin/python3.11"
  [ -x "$PY" ] || { brew install python@3.11; }
  "$PY" -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python dub.py "$@"
