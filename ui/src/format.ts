// Rule 16: only the digits a number deserves.  Every number in the UI passes
// through here, never through toFixed() in a component.
import { C } from "./constants";

export type TimeUnit = { label: string; scale: number };
export const TIME_UNITS: TimeUnit[] = C.units.time;

/** 3 significant / 2-3 decimals per the magnitude table, trailing zeros stripped. */
export function num(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  let s: string;
  if (a === 0) s = "0";
  else if (a >= 1e6 || a < 1e-4) s = v.toExponential(2);
  else if (a >= 1) s = v.toFixed(2);
  else if (a >= 0.1) s = v.toFixed(3);
  else s = v.toPrecision(3);
  if (s.includes(".") && !s.includes("e")) s = s.replace(/0+$/, "").replace(/\.$/, "");
  const [int, frac] = s.split(".");
  if (s.includes("e")) return s;
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return frac ? `${grouped}.${frac}` : grouped;
}

/** Pick the unit that puts |seconds| in [1, 1000). */
export function autoUnit(seconds: number): TimeUnit {
  const a = Math.abs(seconds);
  for (const u of TIME_UNITS) if (a >= u.scale) return u;
  return TIME_UNITS[TIME_UNITS.length - 1];
}

export function unitByLabel(label: string): TimeUnit {
  return TIME_UNITS.find((u) => u.label === label) ?? TIME_UNITS[2];
}

/** {value, unit} for typesetting: <strong>{value} <small>{unit}</small></strong> */
export function time(seconds: number | null | undefined, unit?: TimeUnit): { value: string; unit: string } {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return { value: "—", unit: "" };
  const u = unit ?? autoUnit(seconds === 0 ? 1e-6 : seconds);
  return { value: num(seconds / u.scale), unit: u.label };
}

export const timeText = (s: number | null | undefined, unit?: TimeUnit) => {
  const t = time(s, unit);
  return t.unit ? `${t.value} ${t.unit}` : t.value;
};

/** A measured time: as many decimals as one sample period resolves, no more.
 *  2.1884 ms at 10 ns resolution -> "2.1884 ms" (not "2.19 ms").  Unknown
 *  resolution falls back to 1 ns, the precision Logic 2 prints in its CSV. */
export function measured(seconds: number | null | undefined, resolution: number | null | undefined): { value: string; unit: string } {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return { value: "—", unit: "" };
  const res = resolution && resolution > 0 ? resolution : 1e-9;
  const u = seconds === 0 ? autoUnit(res) : autoUnit(seconds);
  const decimals = Math.min(6, Math.max(0, Math.ceil(Math.log10(u.scale / res) - 1e-9)));
  let s = (seconds / u.scale).toFixed(decimals);
  if (s.includes(".")) s = s.replace(/0+$/, "").replace(/\.$/, "");
  if (s === "-0") s = "0";
  const [int, frac] = s.split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  return { value: frac ? `${grouped}.${frac}` : grouped, unit: u.label };
}

export const measuredText = (s: number | null | undefined, res: number | null | undefined) => {
  const t = measured(s, res);
  return t.unit ? `${t.value} ${t.unit}` : t.value;
};

export function signedTime(seconds: number): string {
  const t = time(seconds);
  return `${seconds > 0 ? "+" : ""}${t.value} ${t.unit}`;
}

export function rate(hz: number | null | undefined): string {
  if (!hz) return "rate unknown";
  if (hz >= 1e6) return `${num(hz / 1e6)} MS/s`;
  if (hz >= 1e3) return `${num(hz / 1e3)} kS/s`;
  return `${num(hz)} S/s`;
}

export function bytes(n: number): string {
  if (n >= 1e9) return `${num(n / 1e9)} GB`;
  if (n >= 1e6) return `${num(n / 1e6)} MB`;
  if (n >= 1e3) return `${num(n / 1e3)} kB`;
  return `${n} B`;
}

/** German-locale safe: accepts "2,5" and "2.5"; empty -> null. */
export function parseDecimal(text: string): number | null {
  const t = text.trim().replace(",", ".");
  if (t === "") return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : NaN;
}

/** Nice tick step for an axis span. */
export function niceStep(span: number, maxTicks: number): number {
  const raw = span / Math.max(1, maxTicks);
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}
