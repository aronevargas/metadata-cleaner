#!/usr/bin/env sh
# Adds "Metadata Editor" to the Linux application menu (~/.local/share/applications) for this checkout.
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$DEST"
cat > "$DEST/metadata-editor.desktop" <<EOD
[Desktop Entry]
Type=Application
Name=Metadata Editor
Comment=Remove metadados e gera cópias numeradas de imagens e vídeos
Exec="$DIR/metadata-editor"
Path=$DIR
Icon=image-x-generic
Terminal=false
Categories=Graphics;AudioVideo;Utility;
EOD
chmod +x "$DEST/metadata-editor.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DEST" >/dev/null 2>&1 || true
echo "Atalho instalado: $DEST/metadata-editor.desktop"
