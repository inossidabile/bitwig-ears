#!/bin/sh
# Build the CLAP plugin and wrap it as a macOS .clap bundle (ad-hoc signed).
#   ./bundle-macos.sh            build + bundle into target/BitwigEarsTap.clap
#   ./bundle-macos.sh --install  also copy it to ~/Library/Audio/Plug-Ins/CLAP
set -e
cd "$(dirname "$0")"
cargo build --release
B="target/BitwigEarsTap.clap"
rm -rf "$B"
mkdir -p "$B/Contents/MacOS"
cp target/release/libbitwig_ears_tap.dylib "$B/Contents/MacOS/BitwigEarsTap"
cat > "$B/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>BitwigEarsTap</string>
<key>CFBundleIdentifier</key><string>io.bitwig-ears.tap</string>
<key>CFBundleName</key><string>BitwigEarsTap</string>
<key>CFBundlePackageType</key><string>BNDL</string>
<key>CFBundleSignature</key><string>????</string>
<key>CFBundleVersion</key><string>0.1.0</string>
<key>CFBundleShortVersionString</key><string>0.1.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$B"
echo "built $B"
if [ "$1" = "--install" ]; then
  D="$HOME/Library/Audio/Plug-Ins/CLAP"
  mkdir -p "$D"
  rm -rf "$D/BitwigEarsTap.clap"
  cp -R "$B" "$D/"
  codesign -v "$D/BitwigEarsTap.clap"
  echo "installed to $D/BitwigEarsTap.clap"
fi
