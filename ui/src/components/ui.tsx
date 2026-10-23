// Small primitives: segmented control, number-with-unit field, status pill, icons.
import type { ReactNode } from "react";
import { TIME_UNITS } from "../format";

export function Segmented<T extends string>(p: {
  value: T; options: { value: T; label: ReactNode; title?: string }[];
  onChange: (v: T) => void; label: string; disabled?: boolean; testId?: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={p.label} data-test={p.testId}>
      {p.options.map((o) => (
        <button key={o.value} type="button" role="radio" aria-checked={p.value === o.value}
          className={p.value === o.value ? "active" : ""} title={o.title}
          disabled={p.disabled} onClick={() => p.onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Field(p: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode; htmlFor?: string }) {
  return (
    <div className={`field ${p.error ? "has-error" : ""}`}>
      <label className="field-label" htmlFor={p.htmlFor}>{p.label}</label>
      {p.children}
      {p.error ? <div className="field-error" role="alert">{p.error}</div>
        : p.hint ? <div className="field-hint">{p.hint}</div> : null}
    </div>
  );
}

export interface Quantity { text: string; unit: string }

export function TimeInput(p: { id: string; value: Quantity; onChange: (q: Quantity) => void; placeholder?: string; testId?: string }) {
  return (
    <div className="unit-input">
      <input id={p.id} type="text" inputMode="decimal" spellCheck={false} autoComplete="off"
        placeholder={p.placeholder} value={p.value.text} data-test={p.testId}
        onChange={(e) => p.onChange({ ...p.value, text: e.target.value })} />
      <select aria-label="unit" value={p.value.unit} onChange={(e) => p.onChange({ ...p.value, unit: e.target.value })}>
        {TIME_UNITS.map((u) => <option key={u.label} value={u.label}>{u.label}</option>)}
      </select>
    </div>
  );
}

export function Pill(p: { tone: "ok" | "ref" | "miss" | "muted" | "busy"; children: ReactNode }) {
  return <span className={`pill pill-${p.tone}`}>{p.children}</span>;
}

const paths: Record<string, string> = {
  plus: "M12 5v14M5 12h14",
  folder: "M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
  x: "M6 6l12 12M18 6L6 18",
  retry: "M4 12a8 8 0 1 0 2.3-5.7M4 4v4h4",
  download: "M12 4v11m0 0l-4-4m4 4l4-4M5 20h14",
  sun: "M12 4V2m0 20v-2m8-8h2M2 12h2m13.7-5.7 1.4-1.4M4.9 19.1l1.4-1.4m0-11.4L4.9 4.9m14.2 14.2-1.4-1.4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  moon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z",
  file: "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5",
  reveal: "M14 3h7v7M10 14 21 3M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5",
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  target: "M12 3v4m0 10v4M3 12h4m10 0h4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
};

export function Icon(p: { name: keyof typeof paths | string; size?: number }) {
  const s = p.size ?? 14;
  return (
    <svg width={s} height={s} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={paths[p.name] ?? ""} />
    </svg>
  );
}

/** <strong>250.04 <small>µs</small></strong> */
export function Num(p: { v: { value: string; unit: string }; className?: string }) {
  return <strong className={`num ${p.className ?? ""}`}>{p.v.value}{p.v.unit && <small>{p.v.unit}</small>}</strong>;
}
