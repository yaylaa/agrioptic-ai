#!/bin/sh
set -eu
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"
if [ ! -x .venv/bin/python ]; then
  echo 'Create .venv and install requirements-api.txt first. See README.md.' >&2
  exit 1
fi
if [ ! -f frontend/dist/index.html ]; then scripts/frontend.sh build; fi
exec .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
