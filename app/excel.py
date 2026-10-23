"""The Excel workbook: results only, never raw edges.

Sheets
  Results       one row per channel per capture, the reference row highlighted
  Batch matrix  (two or more captures) captures × channels, Δt in each cell
  Settings      every setting, the reference per capture, resolution, app version

Δt is written as a number in the chosen unit (µs by default), with the unit in
the header, so Excel can sort, filter and chart it straight away.
"""
from __future__ import annotations

import datetime as _dt
import math
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.constants import C
from app.measure import Result

X = C.excel
_HEAD_FILL = PatternFill("solid", fgColor=X.header_fill)
_HEAD_FONT = Font(bold=True, color=X.header_font)
_REF_FILL = PatternFill("solid", fgColor=X.ref_fill)
_MISS_FILL = PatternFill("solid", fgColor=X.miss_fill)


def unit_scale(unit: str) -> float:
    for u in C.units.time:
        if u["label"] == unit:
            return u["scale"]
    raise ValueError(f"unknown time unit {unit!r}")


def _header(ws, cols: list[str], widths: list[int] | None = None) -> None:
    ws.append(cols)
    for i, _ in enumerate(cols, 1):
        cell = ws.cell(row=1, column=i)
        cell.fill, cell.font = _HEAD_FILL, _HEAD_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = (widths or [])[i - 1] if widths else X.col_width
    ws.freeze_panes = "A2"


def _num(v: float | None, scale: float, resolution: float | None = None) -> float | None:
    """v in the chosen unit, rounded to what one sample period resolves
    (1 ns when the rate is unknown -- Logic 2's CSV precision).  Without this
    Excel shows binary-float noise such as 2188.3999999999 µs."""
    if v is None:
        return None
    res = resolution if resolution and resolution > 0 else 1e-9
    digits = max(0, min(12, math.ceil(math.log10(scale / res) - 1e-9)))
    return round(v / scale, digits)


def write(results: list[Result], out: Path, unit: str = C.units.default_export_unit) -> Path:
    if not results:
        raise ValueError("nothing to export: no measured captures")
    sc = unit_scale(unit)
    labels = dict(C.measure.statuses)
    wb = Workbook()

    # ---- Results ---------------------------------------------------------
    ws = wb.active
    ws.title = X.sheet_results
    cols = ["Capture", "Channel", "Name", "Status", "Edge", f"Δt [{unit}]",
            "Edge time in capture [s]", f"Bounce before settling [{unit}]",
            f"Next edge (outside window) [{unit}]", "Level at reference", "Edges in capture"]
    w = [X.name_col_width, 9, X.name_col_width, 24, 10, X.col_width, 22, 18, 20, 12, 14]
    _header(ws, cols, w)
    for res in results:
        cap_name = Path(res.file).name
        for r in res.channels:
            rs = res.resolution_s
            ws.append([cap_name, r.index, r.name, labels.get(r.status, r.status), r.edge,
                       _num(r.dt, sc, rs), _num(r.time, 1.0, rs), _num(r.bounce, sc, rs),
                       _num(r.next_dt, sc, rs),
                       r.level_at_ref, r.edges_total])
            row = ws.max_row
            fill = _REF_FILL if r.status == "reference" else (
                _MISS_FILL if r.status in ("no_edge", "out_of_window", "no_data") else None)
            if fill:
                for c in range(1, len(cols) + 1):
                    ws.cell(row=row, column=c).fill = fill
            for c in (6, 8, 9):
                ws.cell(row=row, column=c).number_format = "0.0########"
            ws.cell(row=row, column=7).number_format = "0.000000000"
    ws.auto_filter.ref = ws.dimensions

    # ---- Batch matrix ------------------------------------------------------
    if len(results) > 1:
        wm = wb.create_sheet(X.sheet_matrix)
        chans: dict[int, str] = {}
        for res in results:
            for r in res.channels:
                chans.setdefault(r.index, r.name)
        order = sorted(chans)
        _header(wm, ["Capture", "Reference"] + [f"{chans[i]} [{unit}]" for i in order],
                [X.name_col_width, X.name_col_width] + [X.col_width] * len(order))
        for res in results:
            by = {r.index: r for r in res.channels}
            ref = res.reference
            row = [Path(res.file).name, f"{ref.name} {ref.edge}" if ref else "none"]
            for i in order:
                r = by.get(i)
                row.append(_num(r.dt, sc, res.resolution_s) if r and r.status in ("ok", "reference") else None)
            wm.append(row)
            for c in range(3, len(row) + 1):
                cell = wm.cell(row=wm.max_row, column=c)
                cell.number_format = "0.0########"
                if cell.value is None:
                    cell.fill = _MISS_FILL

    # ---- Settings ----------------------------------------------------------
    st = wb.create_sheet(X.sheet_settings)
    _header(st, ["Setting", "Value"], [34, 60])
    s = results[0].settings
    rows = [
        ("Exported by", f"{C.app.name} {C.app.version}"),
        ("Exported at", _dt.datetime.now().isoformat(timespec="seconds")),
        ("Reference mode", "first channel that changes" if s.ref_mode == "auto" else "manual"),
        ("Reference channel", "auto" if s.ref_mode == "auto" else s.ref_channel),
        ("Reference edge", s.ref_edge),
        ("Reference occurrence", s.occurrence if s.ref_mode == "manual" else "first"),
        ("Search from [s]", "capture start" if s.search_from_s is None else s.search_from_s),
        ("Target edge", s.target_edge),
        ("Rule", "first edge at or after the reference (same sample counts, Δt = 0)"),
        (f"Window [{unit}]", "none" if s.window_s is None else _num(s.window_s, sc)),
        (f"Glitch filter [{unit}]", "off" if not s.glitch_s else _num(s.glitch_s, sc)),
        ("Excluded channels", ", ".join(map(str, s.excluded)) or "none"),
        ("Time unit", unit),
    ]
    for r in rows:
        st.append(list(r))
    st.append([])
    st.append(["Capture", "Reference · time in capture [s] · resolution ±[" + unit + "]"])
    for res in results:
        ref = res.reference
        st.append([Path(res.file).name,
                   (f"{ref.name} {ref.edge} #{ref.occurrence} at {ref.time:.9f} s" if ref else "no reference edge")
                   + (f" · ±{res.resolution_s / sc:.6g}" if res.resolution_s else " · resolution unknown")])
        for n in res.notes:
            st.append(["", n])

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
