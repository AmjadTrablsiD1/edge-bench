#!/usr/bin/env bash
# Build the zips to copy to a Windows PC.  Nothing to compile there: the UI is
# prebuilt here, Python packages install on first start.
#
#   scripts/make_windows_package.sh            -> dist/EdgeBench-<ver>-windows.zip
#                                                 dist/EdgeBench-<ver>-windows-offline.zip
#
# The offline zip carries win_amd64 wheels for CPython 3.12 and 3.13 (numpy 2.5
# has none for 3.11), so a PC behind a proxy needs no internet at all.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
VER="$(python3 -c "import json;print(json.load(open('$SRC/shared/constants.json'))['app']['version'])")"
OUT="$SRC/dist"
STAGE="$(mktemp -d)/EdgeBench"
PIP="$SRC/.venv/bin/pip"

echo "==> UI build"
( cd "$SRC/ui" && npm run build >/dev/null )

echo "==> Staging"
mkdir -p "$STAGE" "$OUT"
rsync -a --exclude '__pycache__' "$SRC/app" "$SRC/server" "$SRC/shared" "$STAGE/"
mkdir -p "$STAGE/ui" "$STAGE/branding"
rsync -a "$SRC/ui/dist" "$STAGE/ui/"
cp "$SRC/main.py" "$SRC/requirements.txt" "$SRC/run-edge-bench.bat" "$SRC/LICENSE" "$STAGE/"
cp "$SRC/branding/icon.ico" "$STAGE/branding/"
cp "$SRC/scripts/windows/"* "$STAGE/"
# Windows line endings for everything a Windows user opens or runs
for f in "$STAGE"/*.bat "$STAGE"/*.txt; do perl -pi -e 's/\r?\n/\r\n/' "$f"; done

rm -f "$OUT/EdgeBench-$VER-windows.zip" "$OUT/EdgeBench-$VER-windows-offline.zip"
( cd "$(dirname "$STAGE")" && zip -qr "$OUT/EdgeBench-$VER-windows.zip" EdgeBench )

echo "==> Wheels for the offline zip"
for v in 3.12 3.13; do
  tag="cp${v/./}"
  "$PIP" download -q --only-binary=:all: --platform win_amd64 --python-version "$v" \
    --implementation cp -r "$SRC/requirements.txt" -d "$STAGE/wheels/$tag"
  # logic2-automation needs pywin32 on Windows; the marker is not evaluated for a foreign platform
  "$PIP" download -q --only-binary=:all: --platform win_amd64 --python-version "$v" \
    --implementation cp pywin32 -d "$STAGE/wheels/$tag"
done
( cd "$(dirname "$STAGE")" && zip -qr "$OUT/EdgeBench-$VER-windows-offline.zip" EdgeBench )

ls -lh "$OUT"/EdgeBench-"$VER"-windows*.zip
