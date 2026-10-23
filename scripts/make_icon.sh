#!/usr/bin/env bash
# Turn one SVG into every icon an app needs.  Rule 14: every app gets an icon.
#
#   make_icon.sh branding/icon.svg "My App" [ui/public]
#
# Produces:
#   <svg dir>/icon.icns              the macOS bundle icon
#   <svg dir>/icon-1024.png          for the README and anywhere else
#   <public>/favicon.svg             the browser tab (copied, stays crisp)
#   <public>/apple-touch-icon.png    180x180, for a page saved to a home screen
#
# and installs the .icns into ~/Desktop/Apps/<App Name>.app if that bundle
# exists, replacing the emoji icon App Launcher generates.
#
# Rasterises with rsvg-convert, ImageMagick or sips -- whichever is installed.
set -euo pipefail

SVG="${1:?usage: make_icon.sh <icon.svg> <App Name> [public dir]}"
APP_NAME="${2:?usage: make_icon.sh <icon.svg> <App Name> [public dir]}"
PUBLIC="${3:-}"
DIR="$(cd "$(dirname "$SVG")" && pwd)"
BUNDLE="$HOME/Desktop/Apps/$APP_NAME.app"

render() {   # render <size> <out.png>
  local size="$1" out="$2"
  if command -v rsvg-convert >/dev/null; then
    rsvg-convert -w "$size" -h "$size" "$SVG" -o "$out"
  elif command -v magick >/dev/null; then
    magick -background none "$SVG" -resize "${size}x${size}" "$out"
  elif command -v sips >/dev/null; then
    sips -s format png "$SVG" --out "$out" >/dev/null
    sips -z "$size" "$size" "$out" >/dev/null
  else
    echo "No SVG rasteriser found (rsvg-convert, magick or sips)." >&2
    exit 1
  fi
}

ICONSET="$(mktemp -d)/icon.iconset"
mkdir -p "$ICONSET"

# The exact sizes macOS stores in an .icns.  A non-standard size (64) is
# silently discarded by iconutil, and a missing one makes Finder upscale a
# smaller image into a blurry mess.
for size in 16 32 128 256 512; do
  render "$size" "$ICONSET/icon_${size}x${size}.png"
  render "$((size * 2))" "$ICONSET/icon_${size}x${size}@2x.png"
done
render 1024 "$DIR/icon-1024.png"

iconutil -c icns "$ICONSET" -o "$DIR/icon.icns"
echo "icns : $DIR/icon.icns"

# Windows .ico, when ImageMagick is around.  The .bat launcher ships whether or
# not it has been tested, so the icon that goes with it ships too.
if command -v magick >/dev/null; then
  magick -background none "$SVG" -define icon:auto-resize=256,128,64,48,32,16 "$DIR/icon.ico"
  echo "ico  : $DIR/icon.ico (untested on Windows, like the .bat)"
fi

if [ -n "$PUBLIC" ]; then
  mkdir -p "$PUBLIC"
  cp "$SVG" "$PUBLIC/favicon.svg"
  render 180 "$PUBLIC/apple-touch-icon.png"
  echo "web  : $PUBLIC/favicon.svg, $PUBLIC/apple-touch-icon.png"
fi

if [ -d "$BUNDLE" ]; then
  mkdir -p "$BUNDLE/Contents/Resources"
  cp "$DIR/icon.icns" "$BUNDLE/Contents/Resources/icon.icns"
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile icon" \
    "$BUNDLE/Contents/Info.plist" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string icon" \
    "$BUNDLE/Contents/Info.plist"
  # Finder caches bundle icons by mtime; without this the old one lingers.
  touch "$BUNDLE"
  echo "app  : $BUNDLE (icon replaced)"
fi
