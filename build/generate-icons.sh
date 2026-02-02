#!/usr/bin/env bash
# Generate platform-specific icon files from the source SVG.
# Requires: inkscape (or rsvg-convert), iconutil (macOS), ImageMagick (convert)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ICON_DIR="$SCRIPT_DIR/icons"
SRC="$ICON_DIR/icon.svg"

if [ ! -f "$SRC" ]; then
    echo "Error: $SRC not found"
    exit 1
fi

echo "Generating PNG sizes from SVG..."

# Prefer rsvg-convert (faster) but fall back to inkscape
if command -v rsvg-convert &>/dev/null; then
    CONVERT_CMD="rsvg"
elif command -v inkscape &>/dev/null; then
    CONVERT_CMD="inkscape"
else
    echo "Error: Need rsvg-convert or inkscape to convert SVG to PNG"
    exit 1
fi

for size in 16 32 48 64 128 256 512 1024; do
    out="$ICON_DIR/icon-${size}.png"
    if [ "$CONVERT_CMD" = "rsvg" ]; then
        rsvg-convert -w "$size" -h "$size" "$SRC" -o "$out"
    else
        inkscape "$SRC" -w "$size" -h "$size" -o "$out"
    fi
    echo "  ${size}x${size} -> $out"
done

# Copy 256px as the canonical PNG
cp "$ICON_DIR/icon-256.png" "$ICON_DIR/icon.png"

# --- macOS .icns ---
if [ "$(uname)" = "Darwin" ]; then
    echo "Generating macOS .icns..."
    ICONSET="$ICON_DIR/icon.iconset"
    mkdir -p "$ICONSET"

    cp "$ICON_DIR/icon-16.png"   "$ICONSET/icon_16x16.png"
    cp "$ICON_DIR/icon-32.png"   "$ICONSET/icon_16x16@2x.png"
    cp "$ICON_DIR/icon-32.png"   "$ICONSET/icon_32x32.png"
    cp "$ICON_DIR/icon-64.png"   "$ICONSET/icon_32x32@2x.png"
    cp "$ICON_DIR/icon-128.png"  "$ICONSET/icon_128x128.png"
    cp "$ICON_DIR/icon-256.png"  "$ICONSET/icon_128x128@2x.png"
    cp "$ICON_DIR/icon-256.png"  "$ICONSET/icon_256x256.png"
    cp "$ICON_DIR/icon-512.png"  "$ICONSET/icon_256x256@2x.png"
    cp "$ICON_DIR/icon-512.png"  "$ICONSET/icon_512x512.png"
    cp "$ICON_DIR/icon-1024.png" "$ICONSET/icon_512x512@2x.png"

    iconutil -c icns "$ICONSET" -o "$ICON_DIR/icon.icns"
    rm -rf "$ICONSET"
    echo "  -> $ICON_DIR/icon.icns"
else
    echo "Skipping .icns generation (not on macOS)"
fi

# --- Windows .ico ---
if command -v convert &>/dev/null; then
    echo "Generating Windows .ico..."
    convert \
        "$ICON_DIR/icon-16.png" \
        "$ICON_DIR/icon-32.png" \
        "$ICON_DIR/icon-48.png" \
        "$ICON_DIR/icon-64.png" \
        "$ICON_DIR/icon-128.png" \
        "$ICON_DIR/icon-256.png" \
        "$ICON_DIR/icon.ico"
    echo "  -> $ICON_DIR/icon.ico"
else
    echo "Skipping .ico generation (ImageMagick 'convert' not found)"
fi

echo "Done."
