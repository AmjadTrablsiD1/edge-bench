"""Things that only break on Windows, checked on any machine.

The first real Windows 11 run died before the UI appeared: Python on Windows
reads text as cp1252 unless told otherwise, constants.json holds "⏱", "µs" and
"Δt", and pythonw.exe swallowed the traceback.  macOS defaults to UTF-8, so no
test here saw it.  Python can flag every implicit-encoding open
(-X warn_default_encoding); these tests run the app's real work with that
warning turned into an error for our own code.
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"

_SCRIPT = r"""
import sys, warnings, tempfile
from pathlib import Path
# error for our code only; third-party libraries are not ours to fix
warnings.filterwarnings("error", category=EncodingWarning, module=r"^(app|server|main|__main__)(\.|$)")
sys.path.insert(0, sys.argv[1])
fix, tmp = Path(sys.argv[2]), Path(tempfile.mkdtemp())

import main                                   # what the launcher imports
from app import cache, compare, excel, logic2, measure
from app.readers import csv_digital
import server.api                             # noqa: F401

a = csv_digital.load(fix / "power_up_sequence.csv")
b = csv_digital.load(fix / "power_up_new_fw_rewired.csv")
s = measure.Settings(ref_channel=0)
excel.write([measure.run(a, s)], tmp / "r.xlsx")
excel.write_comparison(compare.compare(a, b, s, match="name"), tmp / "c.xlsx")

cache.root = lambda: tmp                      # export cache bookkeeping
with cache.slot(fix / "power_up_sequence.csv") as d:
    (d / "x").write_bytes(b"0")
logic2.SETTINGS = tmp / "settings.json"       # Logic 2 path setting round trip
logic2.save_path(r"C:\Program Files\Logic\Logic.exe")
assert logic2.configured_path().endswith("Logic.exe")
print("OK")
"""


def test_no_implicit_text_encoding_anywhere_in_the_app():
    out = subprocess.run([sys.executable, "-X", "warn_default_encoding", "-c", _SCRIPT, str(ROOT), str(FIX)],
                         capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert out.returncode == 0 and out.stdout.strip().endswith("OK"), out.stderr[-3000:]


def test_the_old_bug_is_real():
    """Proof the guard matters: constants.json cannot be read as cp1252."""
    with pytest.raises(UnicodeDecodeError):
        (ROOT / "shared" / "constants.json").read_text(encoding="cp1252")


def test_launcher_check_mode_passes_without_utf8_mode():
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env.update({"http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
                "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"})
    out = subprocess.run([sys.executable, "-X", "warn_default_encoding", "-W", "error::EncodingWarning",
                          "main.py", "--check"], capture_output=True, text=True, encoding="utf-8",
                         cwd=ROOT, env=env)
    if not (ROOT / "ui" / "dist" / "index.html").is_file():
        pytest.skip("UI not built")
    assert out.returncode == 0, out.stdout + out.stderr
    assert ": OK" in out.stdout


def test_local_probe_ignores_a_corporate_proxy(monkeypatch):
    """With a dead proxy configured, the launcher must still see its own server."""
    import main

    class Who(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"token": "edge-bench-1"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Who)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for k in ("http_proxy", "HTTP_PROXY", "all_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    try:
        assert main.probe(srv.server_address[1], "edge-bench-1", "127.0.0.1", timeout=2)
    finally:
        srv.shutdown()


def test_fatal_leaves_a_log(monkeypatch, tmp_path, capsys):
    import main
    monkeypatch.setattr(main, "FALLBACK_LOG", tmp_path)
    main.fatal("Traceback (most recent call last):\nUnicodeDecodeError: 'charmap' codec can't decode")
    assert "UnicodeDecodeError" in (tmp_path / "crash.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("bat", ["run-edge-bench.bat", "scripts/windows/Create desktop shortcut.bat"])
def test_batch_files_have_crlf(bat):
    """cmd.exe can miss GOTO labels in LF-only batch files."""
    raw = (ROOT / bat).read_bytes()
    assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n")
    assert "*.bat text eol=crlf" in (ROOT / ".gitattributes").read_text(encoding="utf-8")
