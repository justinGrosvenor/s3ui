#!/usr/bin/env bash
# Package the PyInstaller output as a Linux AppImage.
# Usage: make-appimage.sh [VERSION]
# Expects dist/S3UI/ to exist (PyInstaller output).
# Requires: appimagetool (https://github.com/AppImage/AppImageKit)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/.."
VERSION="${1:-0.1.0}"
APPDIR="S3UI.AppDir"

if [ ! -d "dist/S3UI" ]; then
    echo "Error: dist/S3UI/ not found. Run PyInstaller first."
    exit 1
fi

# Download appimagetool if not available
if ! command -v appimagetool &>/dev/null; then
    echo "Downloading appimagetool..."
    wget -q "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" \
        -O appimagetool
    chmod +x appimagetool
    APPIMAGETOOL="./appimagetool"
else
    APPIMAGETOOL="appimagetool"
fi

echo "=== Building AppImage ==="

# Clean and create AppDir structure
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"

# Copy PyInstaller output
cp -r dist/S3UI/* "$APPDIR/usr/bin/"

# Desktop file
cp "$BUILD_DIR/s3ui.desktop" "$APPDIR/usr/share/applications/s3ui.desktop"
cp "$BUILD_DIR/s3ui.desktop" "$APPDIR/s3ui.desktop"

# Icons
if [ -f "$BUILD_DIR/icons/icon-256.png" ]; then
    cp "$BUILD_DIR/icons/icon-256.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/s3ui.png"
    cp "$BUILD_DIR/icons/icon-256.png" "$APPDIR/s3ui.png"
fi
if [ -f "$BUILD_DIR/icons/icon.svg" ]; then
    cp "$BUILD_DIR/icons/icon.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/s3ui.svg"
fi

# AppRun symlink
ln -sf usr/bin/S3UI "$APPDIR/AppRun"

# Build the AppImage
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "S3UI-${VERSION}-x86_64.AppImage"

# Clean up
rm -rf "$APPDIR"

echo "=== Done: S3UI-${VERSION}-x86_64.AppImage ==="
