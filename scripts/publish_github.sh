#!/usr/bin/env bash
# Publish a tagged release on GitHub: push main + the tag, then a release vX.Y.Z
# carrying both Windows zips and SHA256SUMS.txt.  Installed copies of Edge Bench
# find it at github.com/<repo>/releases/latest and install it from there.
#
#   scripts/publish_github.sh 0.7.0        (scripts/release.sh 0.7.0 --github calls this)
#
# Needs either the GitHub CLI, logged in (`gh auth login`), or a token with
# "Contents: read and write" for the repository in GITHUB_TOKEN.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SRC"
VER="${1:?usage: scripts/publish_github.sh X.Y.Z}"
TAG="v$VER"
REPO="$(python3 -c "import json;print(json.load(open('shared/constants.json', encoding='utf-8'))['update']['github']['repo'])")"
SMALL="dist/EdgeBench-$VER-windows.zip"
OFFLINE="dist/EdgeBench-$VER-windows-offline.zip"
SUMS="dist/SHA256SUMS-$VER.txt"

git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || { echo "no tag $TAG: run scripts/release.sh $VER first"; exit 1; }
[ -f "$SMALL" ] && [ -f "$OFFLINE" ] || { echo "missing $SMALL or $OFFLINE"; exit 1; }
if command -v gh >/dev/null; then
  gh auth status >/dev/null 2>&1 || { echo "gh is not logged in: run  gh auth login"; exit 1; }
elif [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "Install the GitHub CLI and run  gh auth login,  or set GITHUB_TOKEN"; exit 1
fi

# the checksums every installed copy verifies before it installs anything
( cd dist && shasum -a 256 "EdgeBench-$VER-windows.zip" "EdgeBench-$VER-windows-offline.zip" ) > "$SUMS"
NOTES="$(python3 - "$VER" <<'PY'
import sys, re
v, out, on = sys.argv[1], [], False
for line in open("CHANGELOG.md", encoding="utf-8"):
    if line.startswith("## "):
        on = re.match(rf"##\s+{re.escape(v)}(\s|$)", line) is not None
        continue
    if on:
        out.append(line.rstrip())
print("\n".join(out).strip())
PY
)"

echo "==> pushing main and $TAG to origin"
git push origin HEAD:main "$TAG"

echo "==> GitHub release $TAG ($REPO)"
if command -v gh >/dev/null; then
  ONE="$(mktemp -d)"                         # the asset's name is its file name
  cp "$SUMS" "$ONE/SHA256SUMS.txt"
  gh release create "$TAG" "$SMALL" "$OFFLINE" "$ONE/SHA256SUMS.txt" --repo "$REPO" \
    --title "Edge Bench $VER" --notes "$NOTES" --latest
else
  API="https://api.github.com/repos/$REPO"
  AUTH=(-H "Authorization: Bearer $GITHUB_TOKEN" -H "Accept: application/vnd.github+json")
  BODY="$(python3 -c 'import json,sys; print(json.dumps({"tag_name": sys.argv[1], "name": "Edge Bench " + sys.argv[2], "body": sys.argv[3], "make_latest": "true"}))' "$TAG" "$VER" "$NOTES")"
  ID="$(curl -fsS "${AUTH[@]}" -X POST "$API/releases" -d "$BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  upload() {
    curl -fsS "${AUTH[@]}" -H "Content-Type: application/octet-stream" --data-binary @"$1" \
      "https://uploads.github.com/repos/$REPO/releases/$ID/assets?name=$2" >/dev/null
    echo "    uploaded $2"
  }
  upload "$SMALL" "$(basename "$SMALL")"
  upload "$OFFLINE" "$(basename "$OFFLINE")"
  upload "$SUMS" SHA256SUMS.txt
fi

# what an installed copy will see
LOC="$(curl -fsS -o /dev/null -w '%{redirect_url}' "https://github.com/$REPO/releases/latest")"
echo "==> releases/latest -> $LOC"
case "$LOC" in */tag/$TAG) echo "==> done: every Edge Bench will offer $VER" ;;
               *) echo "!! latest is not $TAG yet (GitHub can take a minute); check $LOC"; exit 1 ;; esac
