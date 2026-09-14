#!/bin/sh
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_dir=$(mktemp -d "${TMPDIR:-/tmp}/chaski-tests.XXXXXX")
trap 'rm -rf "$test_dir"' EXIT INT TERM
export PYTHONPATH="$repo_dir/src"
export CHASKI_ROOT="$test_dir/root"
export CHASKI_RUNTIME="$test_dir/run"
export PYTHONPYCACHEPREFIX="$test_dir/pycache"
mkdir -p "$CHASKI_ROOT/media" "$CHASKI_RUNTIME"

python3 -m compileall -q "$repo_dir/src"
python3 -m unittest discover -s "$repo_dir/tests" -v
bash -n "$repo_dir/install.sh"
bash -n "$repo_dir/uninstall.sh"
sh -n "$repo_dir/scripts/xsession"
sh -n "$repo_dir/scripts/chaski-web-kiosk-status"
sh -n "$repo_dir/scripts/chaski-web-kiosk-update"
python3 -m py_compile "$repo_dir/scripts/chaski-web-kiosk-network"
if command -v node >/dev/null 2>&1; then
    node --check "$repo_dir/static/app.js"
    node --check "$repo_dir/static/welcome.js"
fi

echo "All CHASKI repository checks passed."
