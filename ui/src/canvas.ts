import { useEffect, useRef, useState } from "react";
import type { WaveSegment } from "./types";

// Chart chrome scales with the container, not the screen (build-app visualise.md).
// 1150 px is the width the sizes in constants.json were tuned at.
export const chartScale = (w: number) => Math.max(1, Math.min(2.2, w / 1150));

/** Read a theme token (CSS variable) at draw time, so a theme switch just redraws. */
export const token = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim() || "#888";

/** Canvas sized to its box at device pixel ratio; draw() works in CSS pixels. */
export function useCanvas(
  draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void,
  deps: unknown[],
) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawRef = useRef(draw);
  drawRef.current = draw;
  const [size, setSize] = useState({ w: 0, h: 0 });

  const render = () => {
    const canvas = ref.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const { width, height } = canvas.getBoundingClientRect();
    const w = Math.max(1, Math.floor(width)), h = Math.max(1, Math.floor(height));
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    const ctx = canvas.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    drawRef.current(ctx, w, h);
  };

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ro = new ResizeObserver(() => {
      const r = canvas.getBoundingClientRect();
      setSize((s) => (s.w === Math.floor(r.width) && s.h === Math.floor(r.height)
        ? s : { w: Math.floor(r.width), h: Math.floor(r.height) }));
      render();
    });
    ro.observe(canvas);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(render, deps);
  return { ref, size };
}


/** One channel's decimated segments as a logic trace between yHi (1) and yLo (0).
 *  Exact segments are a step line; busy buckets are filled blocks (never thinner
 *  than 2 px, so a burst stays visible when zoomed out). */
export function drawSegments(
  ctx: CanvasRenderingContext2D, segs: WaveSegment[], x: (t: number) => number,
  yHi: number, yLo: number, stroke: string, fill: string, k: number,
) {
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 1.5 * k;
  ctx.lineJoin = "miter";
  for (const s of segs) {
    if (s.mode === "exact") {
      let lv = s.level ?? 0;
      ctx.beginPath();
      ctx.moveTo(x(s.begin), lv ? yHi : yLo);
      for (const t of s.times ?? []) {
        const xx = x(t);
        ctx.lineTo(xx, lv ? yHi : yLo);
        lv ^= 1;
        ctx.lineTo(xx, lv ? yHi : yLo);
      }
      ctx.lineTo(x(s.end), lv ? yHi : yLo);
      ctx.stroke();
    } else {
      const levels = s.levels ?? [], counts = s.counts ?? [];
      const xa = x(s.begin), xb = x(s.end), bw = (xb - xa) / Math.max(1, levels.length);
      ctx.fillStyle = fill;
      ctx.beginPath();
      let pen = false;
      for (let b = 0; b < levels.length; b++) {
        const bx = xa + b * bw;
        if (counts[b] > 0) {
          ctx.fillRect(bx, yHi, Math.max(2 * k, bw), yLo - yHi);
          pen = false;
        } else {
          const yy = levels[b] ? yHi : yLo;
          if (!pen) { ctx.moveTo(bx, yy); pen = true; } else ctx.lineTo(bx, yy);
          ctx.lineTo(bx + bw, yy);
        }
      }
      ctx.stroke();
    }
  }
}
