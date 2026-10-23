// The measurement form: what the user typed (text + unit), persisted per
// browser, and its conversion to the server's Settings (seconds, validated).
import { C } from "./constants";
import { parseDecimal, unitByLabel } from "./format";
import type { EdgeKind, Settings } from "./types";
import type { Quantity } from "./components/ui";

export interface Form {
  refMode: "manual" | "auto";
  refChannel: number | null;
  refEdge: EdgeKind;
  occurrence: string;
  searchFrom: Quantity;
  targetEdge: EdgeKind;
  window: Quantity;
  glitch: Quantity;
  excluded: number[];
  exportUnit: string;
}

const KEY = `${C.ui.storage_prefix}.form.v1`;

export const DEFAULT_FORM: Form = {
  refMode: C.measure.default_ref_mode as Form["refMode"],
  refChannel: null,
  refEdge: C.measure.default_ref_edge as EdgeKind,
  occurrence: String(C.measure.default_occurrence),
  searchFrom: { text: "", unit: "ms" },
  targetEdge: C.measure.default_target_edge as EdgeKind,
  window: { text: "", unit: "µs" },
  glitch: { text: "", unit: "ns" },
  excluded: [],
  exportUnit: C.units.default_export_unit,
};

export function loadForm(): Form {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...DEFAULT_FORM, ...JSON.parse(raw) };
  } catch { /* storage blocked */ }
  return DEFAULT_FORM;
}

export function saveForm(f: Form): void {
  try { localStorage.setItem(KEY, JSON.stringify(f)); } catch { /* storage blocked */ }
}

export type FormErrors = Partial<Record<"occurrence" | "searchFrom" | "window" | "glitch" | "refChannel", string>>;

function seconds(q: Quantity): number | null {
  const v = parseDecimal(q.text);
  if (v === null) return null;
  return v * unitByLabel(q.unit).scale;
}

/** Form -> Settings, or the per-field reasons it cannot be. */
export function toSettings(f: Form): { settings: Settings | null; errors: FormErrors } {
  const errors: FormErrors = {};
  const occ = Number(f.occurrence.trim());
  if (f.refMode === "manual" && (!Number.isInteger(occ) || occ < 1 || occ > C.measure.max_occurrence))
    errors.occurrence = `Whole number, 1 or more.`;
  const from = seconds(f.searchFrom);
  if (Number.isNaN(from)) errors.searchFrom = "Not a number.";
  const win = seconds(f.window);
  if (Number.isNaN(win)) errors.window = "Not a number.";
  else if (win !== null && win <= 0) errors.window = "Must be more than 0, or empty for no window.";
  const gl = seconds(f.glitch);
  if (Number.isNaN(gl)) errors.glitch = "Not a number.";
  else if (gl !== null && gl < 0) errors.glitch = "Cannot be negative.";
  if (f.refMode === "manual" && f.refChannel === null) errors.refChannel = "Choose a reference channel.";
  if (Object.keys(errors).length) return { settings: null, errors };
  return {
    errors,
    settings: {
      ref_mode: f.refMode,
      ref_channel: f.refMode === "manual" ? f.refChannel : null,
      ref_edge: f.refEdge,
      occurrence: f.refMode === "manual" ? occ : 1,
      search_from_s: from,
      target_edge: f.targetEdge,
      window_s: win,
      glitch_s: gl ?? 0,
      excluded: f.excluded,
    },
  };
}
