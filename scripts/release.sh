#!/usr/bin/env bash
# Make a release: bump the version, test, build the Windows zips, commit and tag.
#
#   scripts/release.sh 0.4.0                       test, build, commit, tag
#   scripts/release.sh 0.4.0 --publish DIR         ... and copy the zips to DIR
#                                                  (the update folder the PCs watch)
#   scripts/release.sh 0.4.0 --no-tests            skip pytest + Playwright (not advised)
#
# Write the "## 0.4.0 — <date>" section in CHANGELOG.md first: the app shows it
# to users as "What's new", and this script refuses to release without it.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SRC"

VER="${1:-}"
shift || true
PUBLISH="" TESTS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --publish) PUBLISH="$2"; shift 2 ;;
    --no-tests) TESTS=0; shift ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done

[[ "$VER" =~ ^[0-9]+(\.[0-9]+)*$ ]] || { echo "usage: scripts/release.sh X.Y.Z [--publish DIR] [--no-tests]"; exit 2; }
CUR="$(python3 -c "import json;print(json.load(open('shared/constants.json', encoding='utf-8'))['app']['version'])")"
python3 -c "import sys; v=lambda s: tuple(map(int, s.split('.'))); sys.exit(0 if v('$VER') > v('$CUR') else 1)" \
  || { echo "$VER is not newer than $CUR"; exit 1; }
grep -qE "^## $VER( |$)" CHANGELOG.md || { echo "CHANGELOG.md has no '## $VER — <date>' section yet"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "commit or stash your changes first"; git status --short; exit 1; }
[ -z "$PUBLISH" ] || [ -d "$PUBLISH" ] || { echo "publish folder $PUBLISH does not exist"; exit 1; }

echo "==> $CUR -> $VER"
python3 - "$VER" <<'PY'
import json, sys
from pathlib import Path
p = Path("shared/constants.json")
d = json.loads(p.read_text(encoding="utf-8"))
d["app"]["version"] = sys.argv[1]
p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

if [ "$TESTS" = 1 ]; then
  echo "==> UI build + tests"
  ( cd ui && npm run build >/dev/null )
  .venv/bin/python -m pytest -q
  npx playwright test
fi

echo "==> Windows zips"
bash scripts/make_windows_package.sh

git commit -qam "Release $VER"
git tag -a "v$VER" -m "Edge Bench $VER"
echo "==> committed and tagged v$VER"

if [ -n "$PUBLISH" ]; then
  cp "dist/EdgeBench-$VER-windows.zip" "dist/EdgeBench-$VER-windows-offline.zip" "$PUBLISH/"
  echo "==> copied to $PUBLISH -- PCs watching that folder will offer $VER"
fi
