#!/usr/bin/env bash
# Sign and notarize the macOS .app bundle and .dmg.
# Expected to run in CI after PyInstaller has produced dist/S3UI.app.
#
# Required environment variables:
#   APPLE_ID                   - Apple ID email for notarization
#   APPLE_TEAM_ID              - Team ID from Apple Developer portal
#   APPLE_APP_SPECIFIC_PASSWORD - App-specific password for notarization
#
# The signing certificate must already be imported into a keychain
# (handled by the CI workflow before calling this script).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/.."
VERSION="${1:-${GITHUB_REF_NAME:-0.1.0}}"
IDENTITY="${CODESIGN_IDENTITY:-Developer ID Application}"

APP_PATH="dist/S3UI.app"
DMG_PATH="S3UI-${VERSION}-macos.dmg"

if [ ! -d "$APP_PATH" ]; then
    echo "Error: $APP_PATH not found. Run PyInstaller first."
    exit 1
fi

echo "=== Signing $APP_PATH ==="
codesign --deep --force --verify --verbose \
    --sign "$IDENTITY" \
    --options runtime \
    --entitlements "$BUILD_DIR/entitlements.plist" \
    "$APP_PATH"

echo "=== Verifying signature ==="
codesign --verify --verbose "$APP_PATH"

echo "=== Creating DMG ==="
hdiutil create -volname "S3UI" -srcfolder "$APP_PATH" \
    -ov -format UDZO "$DMG_PATH"

echo "=== Signing DMG ==="
codesign --force --verify --verbose \
    --sign "$IDENTITY" \
    "$DMG_PATH"

# Notarize if credentials are available
if [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" ]; then
    echo "=== Submitting for notarization ==="
    xcrun notarytool submit "$DMG_PATH" \
        --apple-id "$APPLE_ID" \
        --team-id "$APPLE_TEAM_ID" \
        --password "$APPLE_APP_SPECIFIC_PASSWORD" \
        --wait

    echo "=== Stapling notarization ticket ==="
    xcrun stapler staple "$DMG_PATH"
else
    echo "Skipping notarization (credentials not set)"
fi

echo "=== Done: $DMG_PATH ==="
