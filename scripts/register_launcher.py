#!/usr/bin/env python3
"""Add or update this app's tile in App Launcher (~/.config/app-launcher/apps.json).

Copy this file into an app's scripts/ directory and call it from install.sh,
so every app Amjad gets shows up in the Launcher without him doing anything.

It is idempotent: an entry with the same --id is *updated*, never duplicated,
and fields you do not pass keep whatever value they already had.  If App
Launcher is not installed it says so and exits 0, so install.sh still
succeeds on a fresh machine.

    python3 register_launcher.py --id myapp --name "My App" --icon 🛰 \
        --cwd ~/.local/share/myapp \
        --command "$HOME/.local/share/myapp/.venv/bin/python3 $HOME/.local/share/myapp/main.py" \
        --url http://127.0.0.1:8000 --bundle

    python3 register_launcher.py --list
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REGISTRY = Path.home() / ".config" / "app-launcher" / "apps.json"
LAUNCHER_CODE = Path.home() / ".local" / "share" / "app-launcher"

DEFAULTS = {
    "id": "", "name": "", "icon": "\U0001F680", "category": "Apps",
    "description": "", "cwd": "", "command": "", "terminal": False,
    "url": "", "url_wait": 4.0, "env": {},
}


def load(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "apps": []}
    with path.open() as fh:
        data = json.load(fh)
    data.setdefault("version", 1)
    data.setdefault("apps", [])
    return data


def save(path: Path, data: dict) -> None:
    """Write via a temp file so an interrupted run cannot truncate the registry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    tmp.replace(path)


def build_bundle(entry: dict) -> str:
    """Build the .app via App Launcher's own mkapp, so icons match the tile."""
    if not (LAUNCHER_CODE / "launcher" / "mkapp.py").exists():
        return "App Launcher code not found - skipped .app bundle"
    sys.path.insert(0, str(LAUNCHER_CODE))
    try:
        from launcher.registry import Entry      # type: ignore
        from launcher import mkapp               # type: ignore
        known = {f: entry[f] for f in DEFAULTS if f in entry}
        return f"built {mkapp.build(Entry(**known))}"
    except Exception as exc:                     # noqa: BLE001 - report, never fail install
        return f"could not build .app bundle: {exc}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--id")
    p.add_argument("--name")
    p.add_argument("--icon")
    p.add_argument("--category")
    p.add_argument("--description")
    p.add_argument("--cwd")
    p.add_argument("--command")
    p.add_argument("--url", help="web apps: open this once the server answers")
    p.add_argument("--url-wait", type=float, dest="url_wait")
    p.add_argument("--terminal", action=argparse.BooleanOptionalAction, default=None,
                   help="run in Terminal.app instead of headless")
    p.add_argument("--env", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--bundle", action="store_true", help="also build the .app")
    p.add_argument("--remove", action="store_true", help="delete the tile")
    p.add_argument("--list", action="store_true", help="show registered tiles")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--registry", type=Path, default=REGISTRY)
    a = p.parse_args()

    if not a.registry.exists() and not a.list:
        print(f"App Launcher not installed ({a.registry} missing) - skipping tile.")
        return 0

    data = load(a.registry)
    apps = data["apps"]

    if a.list:
        for e in apps:
            print(f"  {e.get('icon','')} {e.get('id','?'):24s} {e.get('name','')}")
        return 0

    if not a.id:
        p.error("--id is required")

    if a.remove:
        data["apps"] = [e for e in apps if e.get("id") != a.id]
        if len(data["apps"]) == len(apps):
            print(f"no tile with id {a.id!r}")
            return 0
        if not a.dry_run:
            save(a.registry, data)
        print(f"removed tile {a.id!r}")
        return 0

    # Only fields actually passed are written, so an update never clobbers a
    # value that is already there.  env is handled apart: it arrives as a list
    # of KEY=VALUE and an empty one must mean "leave it alone", not "wipe it".
    fields = {k: v for k, v in vars(a).items()
              if k in DEFAULTS and k != "env" and v is not None}
    if a.env:
        fields["env"] = dict(kv.split("=", 1) for kv in a.env)
    for key in ("cwd", "command"):
        if key in fields:
            fields[key] = os.path.expanduser(fields[key])

    existing = next((e for e in apps if e.get("id") == a.id), None)
    if existing is None:
        entry = {**DEFAULTS, **fields}
        entry.setdefault("name", a.id)
        apps.append(entry)
        action = "added"
    else:
        existing.update(fields)
        entry = existing
        action = "updated"

    if a.dry_run:
        print(f"[dry run] would have {action} tile:\n{json.dumps(entry, indent=2, ensure_ascii=False)}")
        return 0

    save(a.registry, data)
    print(f"{action} App Launcher tile {entry.get('icon','')} {entry['name']!r}")
    if a.bundle:
        print("  " + build_bundle(entry))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
