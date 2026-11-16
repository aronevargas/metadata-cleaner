#!/usr/bin/env bash
# Headless UI smoke against a fresh server. Needs node + playwright (NODE_PATH) and a Chromium.
#   PW_NODE_MODULES=/path/node_modules CHROME_BIN=/path/chrome-headless-shell tests/ui/run_smoke.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d)"; PORT="${PORT:-8799}"
mkdir -p "$WORK/in" "$WORK/shots"
ROOT="$ROOT" "$ROOT/.venv/bin/python" -I -c "
import sys, os; sys.path.insert(0, os.environ['ROOT'] + '/tests')
from pathlib import Path; from conftest import make_jpeg, make_png, make_mp4
d = Path('$WORK/in'); make_jpeg(d/'foto.JPG', orientation=6); make_png(d/'logo.png'); make_mp4(d/'clip.mp4', seconds=1)
(d/'bad.jpg').write_bytes(b'lixo'); (d/'x.txt').write_text('x')"
cd "$ROOT"; METAEDIT_NO_PICKER=1 .venv/bin/python -m app --port "$PORT" --no-browser >"$WORK/server.log" 2>&1 &
SP=$!; trap 'kill $SP 2>/dev/null' EXIT; sleep 2
NODE_PATH="${PW_NODE_MODULES:-}" node tests/ui/smoke.cjs "http://127.0.0.1:$PORT" "$WORK/in" "$WORK/out" "$WORK/shots"
echo "outputs: $(ls "$WORK/out" | tr '\n' ' ')"; echo "screenshots: $WORK/shots"
