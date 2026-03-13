#!/bin/bash
# Build diskpeek.app + diskpeek.dmg with size optimizations.
set -e

APP="dist/diskpeek.app"
FW="$APP/Contents/Frameworks"
QT6="$FW/PyQt6/Qt6"

echo "==> Building with PyInstaller…"
pyinstaller diskpeek.spec --noconfirm

echo "==> Stripping unused Qt frameworks…"
# PDF viewer — not used
rm -rf "$QT6/lib/QtPdf.framework"
# Network stack — not used (also removes need for libcrypto/libssl)
rm -rf "$QT6/lib/QtNetwork.framework"
rm -f  "$FW/libcrypto.3.dylib"
rm -f  "$FW/libssl.3.dylib"
# SVG — not used
rm -rf "$QT6/lib/QtSvg.framework"
# Compression lib only needed by QtNetwork
rm -f  "$FW/libzstd.1.dylib"

echo "==> Stripping unused Qt plugins…"
# Image formats: keep only the basics (png, jpg, gif); drop tiff, webp, etc.
IMGFMT="$QT6/plugins/imageformats"
for f in "$IMGFMT"/*.dylib; do
  name=$(basename "$f")
  case "$name" in
    libqpng*|libqjpeg*|libqgif*|libqico*) ;;   # keep
    *) rm -f "$f" ;;
  esac
done
# Generic input plugins — not needed for a desktop file-manager app
rm -rf "$QT6/plugins/generic"
# Icon engines (SVG icons) — not needed
rm -rf "$QT6/plugins/iconengines"
# Translations — English only is fine
rm -rf "$QT6/translations"

echo "==> Re-signing app (ad-hoc)…"
codesign --force --deep --sign - "$APP"

echo "==> Size after stripping:"
du -sh "$APP"

echo "==> Building DMG…"
rm -f dist/diskpeek.dmg
create-dmg \
  --volname "diskpeek" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 100 \
  --icon "diskpeek.app" 180 170 \
  --hide-extension "diskpeek.app" \
  --app-drop-link 420 170 \
  "dist/diskpeek.dmg" \
  "$APP"

echo "==> Done!"
du -sh dist/diskpeek.dmg
