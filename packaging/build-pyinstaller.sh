#!/usr/bin/env sh
# Builds the standalone folder app with PyInstaller for the CURRENT platform (tested on Linux x86_64).
# Windows: use packaging\windows\build-exe.ps1 instead (same spec file).
# Output: dist/metadata-editor/metadata-editor  (FFmpeg is NOT bundled; uses the system one or ./ffmpeg/ next to it.)
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
PY="${PYTHON:-$DIR/.venv/bin/python}"
"$PY" -m pip install -q -r "$DIR/requirements-build.txt"
"$PY" -m PyInstaller --noconfirm --clean --distpath "$DIR/dist" --workpath "$DIR/build" \
  "$DIR/packaging/metadata-editor.spec"
echo "Pronto: $DIR/dist/metadata-editor/metadata-editor"
echo "Teste rápido: $DIR/dist/metadata-editor/metadata-editor --self-check relatorio.json"
