#!/bin/bash
# Build diskpeek (Swift) → .app → .dmg
set -e
cd "$(dirname "$0")"

APP="dist/diskpeek.app"
EXE="$APP/Contents/MacOS/diskpeek"
FW="$APP/Contents/Frameworks"

echo "==> Building Swift release binary…"
swift build -c release 2>&1

echo "==> Assembling .app bundle…"
rm -rf dist && mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Frameworks" "$APP/Contents/Resources"

cp .build/release/diskpeek "$EXE"

# Bundle the Go scanner if it exists in the parent project
SCANNER="../scanner/diskpeek-scanner"
[ -f "$SCANNER" ] && cp "$SCANNER" "$FW/diskpeek-scanner" && echo "   Bundled diskpeek-scanner"

cat > "$APP/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>       <string>diskpeek</string>
    <key>CFBundleIdentifier</key>       <string>com.diskpeek.app</string>
    <key>CFBundleName</key>             <string>diskpeek</string>
    <key>CFBundleShortVersionString</key><string>2.0.0</string>
    <key>CFBundleVersion</key>          <string>2</string>
    <key>NSHighResolutionCapable</key>  <true/>
    <key>LSMinimumSystemVersion</key>   <string>14.0</string>
    <key>NSPrincipalClass</key>         <string>NSApplication</string>
    <key>NSDesktopFolderUsageDescription</key>
        <string>diskpeek needs access to scan folders.</string>
    <key>NSDocumentsFolderUsageDescription</key>
        <string>diskpeek needs access to scan folders.</string>
    <key>NSDownloadsFolderUsageDescription</key>
        <string>diskpeek needs access to scan folders.</string>
</dict>
</plist>
PLIST

echo "==> Signing (ad-hoc)…"
codesign --force --deep --sign - "$APP"

echo "==> App size:"
du -sh "$APP"

echo "==> Building DMG…"
which create-dmg > /dev/null 2>&1 || { echo "   create-dmg not found, skipping DMG"; exit 0; }

create-dmg \
  --volname "diskpeek" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 100 \
  --icon "diskpeek.app" 180 170 \
  --hide-extension "diskpeek.app" \
  --app-drop-link 420 170 \
  "dist/diskpeek.dmg" "$APP" 2>&1

echo "==> Done!"
du -sh dist/diskpeek.dmg
