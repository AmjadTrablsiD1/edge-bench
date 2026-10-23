"""The HTTP layer: routes reach the core, errors come back as sentences."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.api import api

FIX = Path(__file__).parent / "fixtures"
client = TestClient(api)


def _wait_ready(fid: int, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        f = next(x for x in client.get("/api/files").json()["files"] if x["id"] == fid)
        if f["status"] in ("ready", "error"):
            return f
        time.sleep(0.05)
    raise AssertionError("capture never finished loading")


def test_whoami():
    assert client.get("/api/whoami").json()["token"] == "edge-bench-1"


def test_binary_folder_measure_and_waveform(tmp_path):
    added = client.post("/api/files", json={"paths": [str(FIX / "logic8_bin")]}).json()["added"]
    f = _wait_ready(added[0]["id"])
    assert f["status"] == "ready" and len(f["capture"]["channels"]) == 8

    r = client.post("/api/measure", json={"file_id": f["id"], "settings": {"ref_channel": 0}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reference"]["channel"] == 0
    assert {c["status"] for c in body["channels"]} <= {"ok", "reference", "no_edge"}

    w = client.get("/api/waveform", params={"file_id": f["id"], "t0": 0, "t1": 0.01, "px": 800})
    assert w.status_code == 200 and len(w.json()["channels"]) == 8

    out = tmp_path / "with space" / "result.xlsx"
    e = client.post("/api/export", json={"file_ids": [f["id"]], "settings": {"ref_channel": 0},
                                         "unit": "µs", "path": str(out)})
    assert e.status_code == 200, e.text and out.exists()


def test_errors_are_sentences(tmp_path):
    bad = tmp_path / "x.txt"
    bad.write_text("nope")
    f = client.post("/api/files", json={"paths": [str(bad)]}).json()["added"][0]
    assert f["status"] == "error" and "Not a Saleae capture" in f["error"]
    missing = client.post("/api/files", json={"paths": [str(tmp_path / "gone.sal")]}).json()["added"][0]
    assert missing["error"] == "File not found."

    ok = client.post("/api/files", json={"paths": [str(FIX / "logic8_digital.csv")]}).json()["added"][0]
    _wait_ready(ok["id"])
    r = client.post("/api/measure", json={"file_id": ok["id"], "settings": {"ref_channel": 42}})
    assert r.status_code == 422 and "channel 42" in r.json()["detail"]
    r = client.post("/api/measure", json={"file_id": ok["id"], "settings": {"ref_mode": "manual"}})
    assert r.status_code == 422 and "reference channel" in r.json()["detail"]
    r = client.post("/api/measure", json={"file_id": 99999, "settings": {"ref_channel": 0}})
    assert r.status_code == 404


def test_folder_of_captures_is_expanded(tmp_path):
    import shutil
    shutil.copy(FIX / "logic8_digital.csv", tmp_path / "a.csv")
    shutil.copy(FIX / "logic8_digital.csv", tmp_path / "b.csv")
    (tmp_path / "readme.txt").write_text("ignored")
    added = client.post("/api/files", json={"paths": [str(tmp_path)]}).json()["added"]
    assert sorted(Path(a["path"]).name for a in added) == ["a.csv", "b.csv"]
    empty = tmp_path / "empty"
    empty.mkdir()
    r = client.post("/api/files", json={"paths": [str(empty)]})
    assert r.status_code == 400
