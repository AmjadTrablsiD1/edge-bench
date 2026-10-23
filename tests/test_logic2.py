"""End to end through the real Logic 2: .sal -> Automation API -> binary -> measure.

Skipped when Logic 2 is not installed.  The fixtures were captured with Logic 2's
simulated devices, and their binary exports (made by hand in Logic 2) sit next to
them -- so the app's own export must reproduce those files' edges exactly.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import cache, logic2, measure, readers
from app.readers import binary

FIX = Path(__file__).parent / "fixtures"

pytestmark = pytest.mark.skipif(logic2.find_executable() is None, reason="Logic 2 not installed")


@pytest.fixture(scope="module")
def session():
    s = logic2.Logic2()
    yield s
    s.close()


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "root", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize("sal, ref", [("sim_logic8.sal", "logic8_bin"), ("sim_pro16.sal", "pro16_bin")])
def test_sal_export_matches_hand_export(session, tmp_cache, sal, ref):
    steps: list[str] = []
    cap = readers.load(FIX / sal, logic2=session, progress=steps.append)
    assert "Exporting edges (binary)" in steps
    hand = binary.load(FIX / ref)
    assert [c.index for c in cap.channels] == [c.index for c in hand.channels]
    for a, b in zip(cap.channels, hand.channels):
        assert a.segments[0].initial == b.segments[0].initial
        assert np.array_equal(np.asarray(a.segments[0].times), np.asarray(b.segments[0].times))
    assert cap.source == "sal" and cap.sample_rate in (25e6, 100e6)

    # Second load comes from the cache: Logic 2 is not asked again.
    steps.clear()
    again = readers.load(FIX / sal, logic2=None)
    assert steps == [] and again.channels[0].edge_count == cap.channels[0].edge_count
    r = measure.run(again, measure.Settings(ref_channel=0))
    assert r.reference is not None and r.resolution_s == pytest.approx(1 / again.sample_rate)


def test_missing_file_reports_cleanly(session, tmp_cache, tmp_path):
    bad = tmp_path / "not_here.sal"
    with pytest.raises(FileNotFoundError):
        readers.load(bad, logic2=session)
