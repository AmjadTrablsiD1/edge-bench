// The viewport: every channel as a lane, the reference as a gold line, each
// measured edge as a blue marker with its Δt bracket.  Time on the axis is
// relative to the reference, because that is the question being asked.
//
// Waveform data comes decimated from the server (one bucket per pixel at most),
// so a capture of any size draws in the same time.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { chartScale, drawSegments, token, useCanvas } from "../canvas";
import { C } from "../constants";
import { measuredText, niceStep, signedTime, timeText } from "../format";
import type { CaptureSummary, Result, Wave } from "../types";

const W = C.waveform;

export interface View { t0: number; t1: number }

interface Props {
  fileId: number;
  capture: CaptureSummary;
  result: Result | null;
  view: View;
  setView: (v: View) => void;
  selected: number | null;
  onSelect: (index: number) => void;
  excluded: number[];
  windowS: number | null;
  theme: string;
}

interface Geo {
  w: number; h: number; k: number; gutter: number; right: number; top: number;
  laneH: number; plotW: number; axisY: number;
}

function geometry(w: number, h: number, n: number): Geo {
  const k = chartScale(w);
  const gutter = W.label_gutter_px * k;
  const right = 18 * k;
  const top = 22 * k;
  const axis = W.axis_px * k;
  const avail = Math.max(10, h - top - axis);
  const laneH = Math.max(14 * k, Math.min((W.lane_px + W.lane_gap_px) * k, avail / Math.max(1, n)));
  return { w, h, k, gutter, right, top, laneH, plotW: Math.max(10, w - gutter - right), axisY: h - axis };
}

export function Waveform(p: Props) {
  const [wave, setWave] = useState<Wave | null>(null);
  const [loading, setLoading] = useState(false);
  const overlay = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{ x: number; view: View; moved: boolean } | null>(null);
  const channels = p.capture.channels;
  const n = channels.length;

  const { ref, size } = useCanvas((ctx, w, h) => {
    const g = geometry(w, h, n);
    const { t0, t1 } = p.view;
    const x = (t: number) => g.gutter + ((t - t0) / (t1 - t0)) * g.plotW;
    const ref0 = p.result?.reference?.time ?? null;
    const origin = ref0 ?? 0;
    const font = (px: number, mono = false) =>
      `${mono ? 500 : 500} ${px * g.k}px ${mono ? "'JetBrains Mono', monospace" : "Inter, sans-serif"}`;

    ctx.fillStyle = token("plotBg");
    ctx.fillRect(0, 0, w, h);

    // grid + axis, relative to the reference
    const step = niceStep(t1 - t0, Math.floor(g.plotW / (110 * g.k)));
    ctx.strokeStyle = token("plotGrid");
    ctx.lineWidth = 1;
    ctx.fillStyle = token("muted");
    ctx.font = font(10, true);
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const first = Math.ceil((t0 - origin) / step) * step + origin;
    for (let t = first; t <= t1; t += step) {
      const xx = Math.round(x(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(xx, g.top - 6 * g.k); ctx.lineTo(xx, g.axisY); ctx.stroke();
      const rel = Math.abs(t - origin) < step * 1e-6 ? 0 : t - origin;
      ctx.fillText(ref0 === null ? timeText(rel) : (rel === 0 ? "0" : signedTime(rel)), xx, g.axisY + 6 * g.k);
    }

    const byIndex = new Map(p.result?.channels.map((c) => [c.index, c]) ?? []);
    const waveBy = new Map(wave?.channels.map((c) => [c.index, c]) ?? []);

    channels.forEach((ch, i) => {
      const y0 = g.top + i * g.laneH;
      const lane = g.laneH - W.lane_gap_px * g.k * (g.laneH / ((W.lane_px + W.lane_gap_px) * g.k));
      const yHi = y0 + lane * W.high_frac;
      const yLo = y0 + lane * W.low_frac;
      const isRef = p.result?.reference?.channel === ch.index;
      const isSel = p.selected === ch.index;
      const isExcl = p.excluded.includes(ch.index) && !isRef;

      if (isSel) {
        ctx.fillStyle = token("active");
        ctx.fillRect(0, y0, w, g.laneH);
      } else if (i % 2 === 1) {
        ctx.fillStyle = token("plotLane");
        ctx.fillRect(g.gutter, y0, g.plotW, g.laneH);
      }

      // label gutter
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.font = font(11);
      ctx.fillStyle = isRef ? token("q-ref") : isExcl ? token("muted") : token("text");
      const label = ch.name.length > 16 ? ch.name.slice(0, 15) + "…" : ch.name;
      ctx.fillText(label, 12 * g.k, (yHi + yLo) / 2 - 1 * g.k);
      ctx.font = font(9, true);
      ctx.fillStyle = token("muted");
      ctx.textAlign = "right";
      ctx.fillText(isRef ? "REF" : `D${ch.index}`, g.gutter - 10 * g.k, (yHi + yLo) / 2);

      // trace
      const wc = waveBy.get(ch.index);
      const colour = isRef ? token("q-ref") : token("q-trace");
      ctx.save();
      ctx.beginPath();
      ctx.rect(g.gutter, y0, g.plotW, g.laneH);
      ctx.clip();
      ctx.globalAlpha = isExcl ? 0.45 : 1;
      drawSegments(ctx, wc?.segments ?? [], x, yHi, yLo, colour,
        isRef ? token("q-ref-2") : token("q-trace-2"), g.k);
      ctx.restore();

      // measured edge, Δt bracket
      const r = byIndex.get(ch.index);
      if (r && ref0 !== null && (r.status === "ok" || r.status === "out_of_window")) {
        const t = r.status === "ok" ? r.time! : ref0 + (r.next_dt ?? 0);
        const xe = x(t), xr = x(ref0);
        const miss = r.status === "out_of_window";
        const c = miss ? token("q-miss") : token("q-edge");
        ctx.save();
        ctx.beginPath();
        ctx.rect(g.gutter, y0, g.plotW, g.laneH);
        ctx.clip();
        ctx.strokeStyle = c;
        ctx.fillStyle = c;
        ctx.lineWidth = (isSel ? 2.5 : 1.8) * g.k;
        if (miss) ctx.setLineDash([4 * g.k, 3 * g.k]);
        ctx.beginPath(); ctx.moveTo(xe, yHi - 3 * g.k); ctx.lineTo(xe, yLo + 3 * g.k); ctx.stroke();
        ctx.setLineDash([]);
        // Δt bracket, only on the selected lane: drawn on every lane it reads
        // as a high pulse next to the trace.
        if (isSel) {
          const yb = y0 + 3.5 * g.k;
          ctx.lineWidth = 1 * g.k;
          ctx.setLineDash([3 * g.k, 3 * g.k]);
          ctx.beginPath();
          ctx.moveTo(xr, yb); ctx.lineTo(xe, yb);
          ctx.stroke();
          ctx.setLineDash([]);
          ctx.beginPath();
          ctx.moveTo(xe, yb - 3 * g.k); ctx.lineTo(xe, yb + 3 * g.k);
          ctx.moveTo(xr, yb - 3 * g.k); ctx.lineTo(xr, yb + 3 * g.k);
          ctx.stroke();
        }
        const txt = miss ? `next +${measuredText(r.next_dt, p.result!.resolution_s)}` : `Δ ${measuredText(r.dt, p.result!.resolution_s)}`;
        ctx.font = font(isSel ? 10.5 : 10, true);
        ctx.textBaseline = "middle";
        const tw = ctx.measureText(txt).width;
        const right = xe + 6 * g.k + tw < g.gutter + g.plotW;
        ctx.textAlign = right ? "left" : "right";
        const tx = right ? xe + 6 * g.k : xe - 6 * g.k;
        ctx.fillStyle = token("plotBg");
        ctx.fillRect(right ? tx - 2 * g.k : tx - tw - 2 * g.k, (yHi + yLo) / 2 - 7 * g.k, tw + 4 * g.k, 14 * g.k);
        ctx.fillStyle = c;
        ctx.fillText(txt, tx, (yHi + yLo) / 2);
        ctx.restore();
      }
    });

    // window limit
    if (ref0 !== null && p.windowS) {
      const xw = x(ref0 + p.windowS);
      if (xw >= g.gutter && xw <= g.gutter + g.plotW) {
        ctx.strokeStyle = token("q-miss");
        ctx.lineWidth = 1 * g.k;
        ctx.setLineDash([3 * g.k, 4 * g.k]);
        ctx.beginPath(); ctx.moveTo(xw, g.top - 6 * g.k); ctx.lineTo(xw, g.axisY); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = token("q-miss");
        ctx.font = font(9, true);
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        ctx.fillText("WINDOW", xw + 4 * g.k, 6 * g.k);
      }
    }

    // reference line on top of everything
    if (ref0 !== null) {
      const xr = x(ref0);
      if (xr >= g.gutter - 1 && xr <= g.gutter + g.plotW + 1) {
        ctx.strokeStyle = token("q-ref");
        ctx.lineWidth = 1.5 * g.k;
        ctx.setLineDash([6 * g.k, 3 * g.k]);
        ctx.beginPath(); ctx.moveTo(xr, g.top - 8 * g.k); ctx.lineTo(xr, g.axisY); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = token("q-ref");
        ctx.font = font(9, true);
        ctx.textAlign = "left";
        ctx.textBaseline = "top";
        const r = p.result!.reference!;
        ctx.fillText(`REF ${r.name} ${r.edge === "rising" ? "↑" : "↓"}`, xr + 5 * g.k, 6 * g.k);
      }
    }

    // gutter divider
    ctx.strokeStyle = token("line");
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(g.gutter + 0.5, 0); ctx.lineTo(g.gutter + 0.5, h); ctx.stroke();
  }, [wave, p.result, p.view, p.selected, p.excluded, p.windowS, p.theme, n]);

  // fetch the decimated window whenever the view or width changes
  useEffect(() => {
    if (size.w < 10) return;
    const g = geometry(size.w, size.h, n);
    const ctl = new AbortController();
    const t = window.setTimeout(() => {
      setLoading(true);
      api.waveform(p.fileId, p.view.t0, p.view.t1, g.plotW, ctl.signal)
        .then(setWave)
        .catch(() => { /* aborted or transient; next view change retries */ })
        .finally(() => setLoading(false));
    }, 40);
    return () => { window.clearTimeout(t); ctl.abort(); };
  }, [p.fileId, p.view.t0, p.view.t1, size.w, size.h, n]);

  useEffect(() => { setWave(null); }, [p.fileId]);

  // ---- interaction: hover crosshair (imperative), zoom, pan, select --------
  const xToT = (clientX: number) => {
    const c = overlay.current!;
    const r = c.getBoundingClientRect();
    const g = geometry(r.width, r.height, n);
    return p.view.t0 + ((clientX - r.left - g.gutter) / g.plotW) * (p.view.t1 - p.view.t0);
  };

  const drawHover = (clientX: number | null, clientY?: number) => {
    const c = overlay.current;
    if (!c) return;
    const r = c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.floor(r.width * dpr)) { c.width = Math.floor(r.width * dpr); c.height = Math.floor(r.height * dpr); }
    const ctx = c.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    if (clientX === null) return;
    const g = geometry(r.width, r.height, n);
    const xx = clientX - r.left;
    if (xx < g.gutter || xx > g.gutter + g.plotW || (clientY ?? 0) - r.top > g.axisY) return;
    ctx.strokeStyle = token("secondary");
    ctx.globalAlpha = 0.6;
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(Math.round(xx) + 0.5, g.top - 6 * g.k); ctx.lineTo(Math.round(xx) + 0.5, g.axisY); ctx.stroke();
    ctx.globalAlpha = 1;
    const t = xToT(clientX);
    const ref0 = p.result?.reference?.time;
    const txt = ref0 === undefined ? timeText(t) : signedTime(t - ref0);
    ctx.font = `500 ${10 * g.k}px 'JetBrains Mono', monospace`;
    const tw = ctx.measureText(txt).width;
    const bx = Math.min(xx + 6 * g.k, g.gutter + g.plotW - tw - 8 * g.k);
    ctx.fillStyle = token("raised");
    ctx.fillRect(bx - 4 * g.k, g.axisY - 20 * g.k, tw + 8 * g.k, 16 * g.k);
    ctx.fillStyle = token("text");
    ctx.textBaseline = "middle";
    ctx.textAlign = "left";
    ctx.fillText(txt, bx, g.axisY - 12 * g.k);
  };

  const onWheel = (e: React.WheelEvent) => {
    const t = xToT(e.clientX);
    const f = Math.pow(W.zoom_step, Math.sign(e.deltaY));
    const full = (p.capture.end - p.capture.begin) * 1.2 || 1;
    let span = Math.min(full, Math.max(W.min_span_s, (p.view.t1 - p.view.t0) * f));
    if (!Number.isFinite(span)) span = full;
    const a = (t - p.view.t0) / (p.view.t1 - p.view.t0);
    p.setView({ t0: t - a * span, t1: t - a * span + span });
  };

  // React attaches wheel listeners as passive; stop the page from scrolling.
  useEffect(() => {
    const c = overlay.current;
    if (!c) return;
    const stop = (e: WheelEvent) => e.preventDefault();
    c.addEventListener("wheel", stop, { passive: false });
    return () => c.removeEventListener("wheel", stop);
  }, []);

  const onDown = (e: React.PointerEvent) => {
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, view: p.view, moved: false };
  };
  const onMove = (e: React.PointerEvent) => {
    drawHover(e.clientX, e.clientY);
    const d = drag.current;
    if (!d) return;
    if (Math.abs(e.clientX - d.x) > 4) d.moved = true;
    if (!d.moved) return;
    const r = overlay.current!.getBoundingClientRect();
    const g = geometry(r.width, r.height, n);
    const dt = ((e.clientX - d.x) / g.plotW) * (d.view.t1 - d.view.t0);
    p.setView({ t0: d.view.t0 - dt, t1: d.view.t1 - dt });
  };
  const onUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (d && !d.moved) {
      const r = overlay.current!.getBoundingClientRect();
      const g = geometry(r.width, r.height, n);
      const i = Math.floor((e.clientY - r.top - g.top) / g.laneH);
      if (i >= 0 && i < n) p.onSelect(channels[i].index);
    }
  };

  return (
    <div className="viewport" data-test="viewport" data-t0={p.view.t0} data-t1={p.view.t1}>
      <canvas ref={ref} className="viewport-host" aria-label="Waveform of every channel with the reference and measured edges" />
      <canvas
        ref={overlay}
        className="viewport-host overlay"
        data-test="wave-overlay"
        onWheel={onWheel}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerLeave={() => drawHover(null)}
      />
      {loading && <div className="viewport-loading" aria-hidden="true" />}
    </div>
  );
}
