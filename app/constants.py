"""Rule 11: every constant lives in shared/constants.json -- this only loads it.

    from app.constants import C
    C.server.host, C["server"]["host"], C.paths.log_dir_path

Adding a constant means editing the JSON, nothing else.  If you are about to
type a number into another module, it belongs here instead.
"""
from __future__ import annotations

import json
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "shared" / "constants.json"


class _Node(dict):
    """A dict that is also reachable with dots, so C.server.port works."""

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(f"no constant {name!r} in {list(self)}") from exc

    @classmethod
    def wrap(cls, value):
        if isinstance(value, dict):
            return cls({k: cls.wrap(v) for k, v in value.items()})
        if isinstance(value, list):
            return [cls.wrap(v) for v in value]
        return value


C = _Node.wrap(json.loads(_FILE.read_text()))


def path(key: str) -> Path:
    """A constant from `paths`, with ~ expanded and the folder created."""
    p = Path(C["paths"][key]).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p
