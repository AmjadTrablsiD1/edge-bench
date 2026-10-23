// Comparison viewport: every channel is a lane with two rows -- A (baseline,
// teal) above, B (new, purple) below -- each capture aligned on its OWN
// reference edge, so t = 0 is "the event" in both.  A shift between the two
// measured edges is shaded in the verdict's colour.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { chartScale, drawSegments, token, useCanvas } from "../canvas";
import { C } from "../constants";
import { measuredText, niceStep, signedTime } from "../format";
import type { CaptureSummary, Comparison, Verdict, Wave } from "../types";
import type { View } from "./Waveform";

const W = C.waveform;

interface Props {
  aId: number; bId: number;
  capA: CaptureSummary; capB: CaptureSummary;
  cmp: Comparison | null;
  view: View;                       // relative to each reference
  setView: (v: View) => void;
  selected: number | null;
  onSelect: (index: number) => void;
  theme: string;
}

export const VERDICT_TOKEN: Record<Verdict, string> = {
  critical: "q-miss", changed: "gold", same: "positive", excluded: "muted", missing: "muted",
};

function geometry(w: number, h: number, n: number) {
  const k = chartScale(w);
  const gutter = W.label_gutter_px * k;
  const right = 18 * k;
  const top = 22 * k;
  const axis = W.axis_px * k;
  const avail = Math.max(10, h - top - axis);
  const laneH = Math.max(26 * k, Math.min((2 * W.lane_px + W.lane_gap_px) * k, avail / Math.max(1, n)));
  return { w, h, k, gutter, right, top, laneH, plotW: Math.max(10, w - gutter - right), axisY: h - axis };
}

export function CompareWave(p: Props) {
  const [waveA, setWaveA] = useState<Wave | null>(null);
  const [waveB, setWaveB] = useState<Wave | null>(null);
  const overlay = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{ x: number; view: View; moved: boolean } | null>(null);

  const indices = p.cmp ? p.cmp.rows.map((r) => r.index)
    : [...new Set([...p.capA.channels, ...p.capB.channels].map((c) => c.index))].sort((a, b) => a - b);
  const n = indices.length;
  const refA = p.cmp?.reference_a?.time ?? p.capA.begin;
  const refB = p.cmp?.reference_b?.time ?? p.capB.begin;

  const { ref, size } = useCanvas((ctx, w, h) => {
    const g = geometry(w, h, n);
    const { t0, t1 } = p.view;
    const x = (t: number) => g.gutter + ((t - t0) / (t1 - t0)) * g.plotW;
    const xa = (t: number) => x(t - refA);
    const xb = (t: number) => x(t - refB);
    const font = (px: number, mono = false) => `500 ${px * g.k}px ${mono ? "'JetBrains Mono', monospace" : "Inter, sans-serif"}`;
    const res = p.cmp?.resolution_s ?? null;

    ctx.fillStyle = token("plotBg");
    ctx.fillRect(0, 0, w, h);

    // grid + axis (time after the reference)
    const step = niceStep(t1 - t0, Math.floor(g.plotW / (110 * g.k)));
    ctx.strokeStyle = token("plotGrid");
    ctx.lineWidth = 1;
    ctx.fillStyle = token("muted");
    ctx.font = font(10, true);
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
      const xx = Math.round(x(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(xx, g.top - 6 * g.k); ctx.lineTo(xx, g.axisY); ctx.stroke();
      ctx.fillText(Math.abs(t) < step * 1e-6 ? "0" : signedTime(t), xx, g.axisY + 6 * g.k);
    }

    const rows = new Map(p.cmp?.rows.map((r) => [r.index, r]) ?? []);
    const wa = new Map(waveA?.channels.map((c) => [c.index, c]) ?? []);
    const wb = new Map(waveB?.channels.map((c) => [c.index, c]) ?? []);
    const nameOf = (i: number) => p.capA.channels.find((c) => c.index === i)?.name
      ?? p.capB.channels.find((c) => c.index === i)?.name ?? `D${i}`;

    indices.forEach((idx, i) => {
      const y0 = g.top + i * g.laneH;
      const gap = g.laneH * W.lane_gap_px / (2 * W.lane_px + W.lane_gap_px);
      const sub = (g.laneH - gap) / 2;
      const row = rows.get(idx);
      const isSel = p.selected === idx;
      const verdict = row?.verdict ?? "same";

      if (isSel) { ctx.fillStyle = token("active"); ctx.fillRect(0, y0, w, g.laneH); }
      else if (i % 2 === 1) { ctx.fillStyle = token("plotLane"); ctx.fillRect(g.gutter, y0, g.plotW, g.laneH); }

      // gutter: verdict dot, name, A/B row tags
      const mid = y0 + sub;
      ctx.fillStyle = token(VERDICT_TOKEN[verdict]);
      ctx.beginPath(); ctx.arc(10 * g.k, mid, 3.5 * g.k, 0, Math.PI * 2); ctx.fill();
      ctx.font = font(11);
      ctx.fillStyle = token("text");
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      const nm = nameOf(idx);
      ctx.fillText(nm.length > 14 ? nm.slice(0, 13) + "…" : nm, 20 * g.k, mid);
      ctx.font = font(9, true);
      ctx.textAlign = "right";
      ctx.fillStyle = token("q-trace");
      ctx.fillText("A", g.gutter - 8 * g.k, y0 + sub / 2);
      ctx.fillStyle = token("q-new");
      ctx.fillText("B", g.gutter - 8 * g.k, y0 + sub + sub / 2);

      const hi = (r0: number) => r0 + sub * 0.22, lo = (r0: number) => r0 + sub * 0.82;
      ctx.save();
      ctx.beginPath(); ctx.rect(g.gutter, y0, g.plotW, g.laneH); ctx.clip();

      // shift between the two measured edges
      const ea = row?.a?.status === "ok" ? row.a.dt : null;
      const eb = row?.b?.status === "ok" ? row.b.dt : null;
      if (ea !== null && eb !== null && verdict !== "same" && row?.dt_delta && Math.abs(x(eb) - x(ea)) > 1) {
        ctx.globalAlpha = 0.2;
        ctx.fillStyle = token(VERDICT_TOKEN[verdict]);
        ctx.fillRect(Math.min(x(ea), x(eb)), y0 + 1, Math.abs(x(eb) - x(ea)), g.laneH - 2);
        ctx.globalAlpha = 1;
      }

      drawSegments(ctx, wa.get(idx)?.segments ?? [], xa, hi(y0), lo(y0), token("q-trace"), token("q-trace-2"), g.k);
      drawSegments(ctx, wb.get(idx)?.segments ?? [], xb, hi(y0 + sub), lo(y0 + sub), token("q-new"), token("q-new-2"), g.k);

      // edge markers + labels
      const mark = (dt: number | null, r0: number, colour: string, label: string) => {
        if (dt === null) return;
        const xe = x(dt);
        ctx.strokeStyle = colour; ctx.fillStyle = colour;
        ctx.lineWidth = (isSel ? 2.4 : 1.8) * g.k;
        ctx.beginPath(); ctx.moveTo(xe, hi(r0) - 2 * g.k); ctx.lineTo(xe, lo(r0) + 2 * g.k); ctx.stroke();
        ctx.font = font(9.5, true);
        const tw = ctx.measureText(label).width;
        const right = xe + 5 * g.k + tw < g.gutter + g.plotW;
        const tx = right ? xe + 5 * g.k : xe - 5 * g.k - tw;
        ctx.fillStyle = token("plotBg");
        ctx.fillRect(tx - 2 * g.k, (hi(r0) + lo(r0)) / 2 - 6 * g.k, tw + 4 * g.k, 12 * g.k);
        ctx.fillStyle = colour;
        ctx.textAlign = "left";
        ctx.fillText(label, tx, (hi(r0) + lo(r0)) / 2);
      };
      mark(ea, y0, token("q-edge"), `A ${measuredText(ea, res)}`);
      const delta = row?.dt_delta ?? null;
      mark(eb, y0 + sub, token("q-new"),
        `B ${measuredText(eb, res)}${delta ? ` (${delta > 0 ? "+" : ""}${measuredText(delta, res)})` : ""}`);
      ctx.restore();
    });

    // reference (t = 0 in both)
    const x0 = x(0);
    if (x0 >= g.gutter && x0 <= g.gutter + g.plotW) {
      ctx.strokeStyle = token("q-ref");
      ctx.lineWidth = 1.5 * g.k;
      ctx.setLineDash([6 * g.k, 3 * g.k]);
      ctx.beginPath(); ctx.moveTo(x0, g.top - 8 * g.k); ctx.lineTo(x0, g.axisY); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = token("q-ref");
      ctx.font = font(9, true);
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      const r = p.cmp?.reference_a;
      ctx.fillText(r ? `REF ${r.name} ${r.edge === "rising" ? "↑" : "↓"} (A and B)` : "REF", x0 + 5 * g.k, 6 * g.k);
    }
    ctx.strokeStyle = token("line");
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(g.gutter + 0.5, 0); ctx.lineTo(g.gutter + 0.5, h); ctx.stroke();
  }, [waveA, waveB, p.cmp, p.view, p.selected, p.theme, n, refA, refB]);

  // fetch both captures' windows, each shifted onto its own reference
  useEffect(() => {
    if (size.w < 10) return;
    const g = geometry(size.w, size.h, n);
    const ctl = new AbortController();
    const t = window.setTimeout(() => {
      api.waveform(p.aId, refA + p.view.t0, refA + p.view.t1, g.plotW, ctl.signal).then(setWaveA).catch(() => {});
      api.waveform(p.bId, refB + p.view.t0, refB + p.view.t1, g.plotW, ctl.signal).then(setWaveB).catch(() => {});
    }, 40);
    return () => { window.clearTimeout(t); ctl.abort(); };
  }, [p.aId, p.bId, refA, refB, p.view.t0, p.view.t1, size.w, size.h, n]);

  // ---- interaction (same gestures as the measure view) ---------------------
  const geo = () => { const r = overlay.current!.getBoundingClientRect(); return { r, g: geometry(r.width, r.height, n) }; };
  const xToT = (clientX: number) => { const { r, g } = geo(); return p.view.t0 + ((clientX - r.left - g.gutter) / g.plotW) * (p.view.t1 - p.view.t0); };

  const hover = (clientX: number | null, clientY = 0) => {
    const c = overlay.current;
    if (!c) return;
    const { r, g } = geo();
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.floor(r.width * dpr)) { c.width = Math.floor(r.width * dpr); c.height = Math.floor(r.height * dpr); }
    const ctx = c.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, r.width, r.height);
    if (clientX === null) return;
    const xx = clientX - r.left;
    if (xx < g.gutter || xx > g.gutter + g.plotW || clientY - r.top > g.axisY) return;
    ctx.strokeStyle = token("secondary"); ctx.globalAlpha = 0.6; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(Math.round(xx) + 0.5, g.top - 6 * g.k); ctx.lineTo(Math.round(xx) + 0.5, g.axisY); ctx.stroke();
    ctx.globalAlpha = 1;
    const txt = signedTime(xToT(clientX));
    ctx.font = `500 ${10 * g.k}px 'JetBrains Mono', monospace`;
    const tw = ctx.measureText(txt).width;
    const bx = Math.min(xx + 6 * g.k, g.gutter + g.plotW - tw - 8 * g.k);
    ctx.fillStyle = token("raised"); ctx.fillRect(bx - 4 * g.k, g.axisY - 20 * g.k, tw + 8 * g.k, 16 * g.k);
    ctx.fillStyle = token("text"); ctx.textBaseline = "middle"; ctx.textAlign = "left";
    ctx.fillText(txt, bx, g.axisY - 12 * g.k);
  };

  useEffect(() => {
    const c = overlay.current;
    if (!c) return;
    const stop = (e: WheelEvent) => e.preventDefault();
    c.addEventListener("wheel", stop, { passive: false });
    return () => c.removeEventListener("wheel", stop);
  }, []);

  const full = Math.max(p.capA.end - p.capA.begin, p.capB.end - p.capB.begin) * 1.2 || 1;
  return (
    <div className="viewport" data-test="compare-viewport" data-t0={p.view.t0} data-t1={p.view.t1}>
      <canvas ref={ref} className="viewport-host" aria-label="Baseline and new capture, channel by channel, aligned on their reference edges" />
      <canvas ref={overlay} className="viewport-host overlay"
        onWheel={(e) => {
          const t = xToT(e.clientX);
          const span = Math.min(full, Math.max(W.min_span_s, (p.view.t1 - p.view.t0) * Math.pow(W.zoom_step, Math.sign(e.deltaY))));
          const a = (t - p.view.t0) / (p.view.t1 - p.view.t0);
          p.setView({ t0: t - a * span, t1: t - a * span + span });
        }}
        onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture(e.pointerId); drag.current = { x: e.clientX, view: p.view, moved: false }; }}
        onPointerMove={(e) => {
          hover(e.clientX, e.clientY);
          const d = drag.current;
          if (!d) return;
          if (Math.abs(e.clientX - d.x) > 4) d.moved = true;
          if (!d.moved) return;
          const { g } = geo();
          const dt = ((e.clientX - d.x) / g.plotW) * (d.view.t1 - d.view.t0);
          p.setView({ t0: d.view.t0 - dt, t1: d.view.t1 - dt });
        }}
        onPointerUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (d && !d.moved) {
            const { r, g } = geo();
            const i = Math.floor((e.clientY - r.top - g.top) / g.laneH);
            if (i >= 0 && i < n) p.onSelect(indices[i]);
          }
        }}
        onPointerLeave={() => hover(null)} />
      {!p.cmp && <div className="viewport-loading" aria-hidden="true" />}
    </div>
  );
}

/** View that shows the reference and every measured edge of both captures. */
export function compareFocus(cmp: Comparison | null): View {
  const dts = (cmp?.rows ?? []).flatMap((r) => [r.a?.status === "ok" ? r.a.dt : null, r.b?.status === "ok" ? r.b.dt : null])
    .filter((v): v is number => v !== null);
  let hi = Math.max(0, ...dts);
  const minSpan = Math.max((cmp?.resolution_s ?? 1e-8) * 40, 1e-6);
  if (hi < minSpan) hi = minSpan * 10;
  const pad = hi * (W.default_span_factor - 1);
  return { t0: -pad, t1: hi + pad };
}

