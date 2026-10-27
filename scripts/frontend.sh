#!/bin/sh
set -eu
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"
runtime_root="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies"
if ! command -v node >/dev/null 2>&1; then
  if [ -x "$runtime_root/node/bin/node" ]; then
    PATH="$runtime_root/node/bin:$PATH"
    export PATH
  else
    echo 'Node.js 22+ is required. Install Node.js and pnpm, then retry.' >&2
    exit 1
  fi
fi
if command -v pnpm >/dev/null 2>&1; then
  package_runner=$(command -v pnpm)
elif [ -x "$runtime_root/bin/fallback/pnpm" ]; then
  package_runner="$runtime_root/bin/fallback/pnpm"
else
  echo 'pnpm is required. Install pnpm, then retry.' >&2
  exit 1
fi
case "${1:-build}" in
  install) exec "$package_runner" install --frozen-lockfile ;;
  build) exec "$package_runner" --dir frontend build ;;
  dev) exec "$package_runner" --dir frontend dev ;;
  *) echo 'Usage: scripts/frontend.sh install|build|dev' >&2; exit 2 ;;
esac
