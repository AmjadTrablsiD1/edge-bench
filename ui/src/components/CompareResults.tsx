// Comparison table (verdict per channel, A vs B) and, beside it, why: every
// reason the core found, plus the side-by-side numbers it compared.
import { useMemo, useState } from "react";
import { measuredText, num } from "../format";
import type { CompareRow, Comparison, Metrics, Verdict } from "../types";
import { Pill } from "./ui";

const TONE: Record<Verdict, "ok" | "ref" | "miss" | "muted" | "busy"> = {
  same: "ok", changed: "busy", critical: "miss", excluded: "muted", missing: "muted",
};
const ORDER: Record<Verdict, number> = { critical: 0, changed: 1, missing: 2, same: 3, excluded: 4 };

const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${num(v * 100)} %`);

export function CompareResults(p: { cmp: Comparison; selected: number | null; onSelect: (i: number) => void }) {
  const [byRisk, setByRisk] = useState(true);
  const rows = useMemo(() => {
    const r = [...p.cmp.rows];
    if (byRisk) r.sort((a, b) => ORDER[a.verdict] - ORDER[b.verdict] || a.index - b.index);
    return r;
  }, [p.cmp, byRisk]);
  const res = p.cmp.resolution_s;
  const sel = p.cmp.rows.find((r) => r.index === p.selected) ?? null;
  const dt = (m: Metrics | null) => (m && (m.status === "ok" || m.status === "reference") ? measuredText(m.dt, res)
    : m ? p.cmp.status_labels[m.status] ?? m.status : "—");

  return (
    <div className="workspace-bottom">
      <div className="table-wrap" data-test="compare-results">
        <table className="results">
          <thead>
            <tr>
              <th className="sortable" aria-sort={byRisk ? "none" : "ascending"} onClick={() => setByRisk(false)}>Ch</th>
              <th>Name</th>
              <th className="sortable" aria-sort={byRisk ? "descending" : "none"} onClick={() => setByRisk(true)} title="Most risky first">Verdict {byRisk ? "▼" : ""}</th>
              <th>Why</th>
              <th className="num-col">B − A</th>
              <th className="num-col">Δt A</th>
              <th className="num-col">Δt B</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.index} className={`${p.selected === r.index ? "selected" : ""} v-${r.verdict}`}
                onClick={() => p.onSelect(r.index)} data-test={`cmp-row-${r.index}`} tabIndex={0}
                onKeyDown={(e) => { if (e.key === "Enter") p.onSelect(r.index); }}>
                <td className="mono">D{r.index}</td>
                <td className="name-cell">{r.name_a ?? r.name_b}{r.name_a && r.name_b && r.name_a !== r.name_b ? ` / ${r.name_b}` : ""}</td>
                <td><Pill tone={TONE[r.verdict]}>{p.cmp.verdict_labels[r.verdict]}</Pill></td>
                <td className="why-cell" title={r.reasons.map((x) => x.text).join("\n")}>
                  {r.reasons.length ? r.reasons[0].text + (r.reasons.length > 1 ? `  (+${r.reasons.length - 1} more)` : "") : ""}
                </td>
                <td className="num-col mono strong" data-test={`cmp-delta-${r.index}`}>
                  {r.dt_delta === null ? "—" : `${r.dt_delta > 0 ? "+" : ""}${measuredText(r.dt_delta, res)}`}
                </td>
                <td className="num-col mono">{dt(r.a)}</td>
                <td className="num-col mono">{dt(r.b)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="evidence-panel" data-test="compare-evidence" tabIndex={0} aria-label="Why this channel was flagged">
        {sel ? <Detail cmp={p.cmp} r={sel} /> : <Summary cmp={p.cmp} />}
      </div>
    </div>
  );
}

function Summary({ cmp }: { cmp: Comparison }) {
  const s = cmp.summary;
  return (
    <div>
      <div className="eyebrow">Comparison</div>
      <div className="verdict-grid" data-test="compare-summary">
        <div className="vcell critical"><b>{s.critical}</b><span>critical</span></div>
        <div className="vcell changed"><b>{s.changed}</b><span>changed</span></div>
        <div className="vcell same"><b>{s.same}</b><span>same</span></div>
      </div>
      <p className="body-text">
        {s.critical ? "Critical: an edge appeared or vanished, flipped direction, the level at the reference differs, or a channel started or stopped toggling."
          : s.changed ? "No structural change; some timing moved beyond tolerance."
          : "Nothing moved beyond tolerance."} Click a row for the details.
      </p>
      {cmp.notes.length > 0 && <ul className="cover-list">{cmp.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
    </div>
  );
}

function Detail({ cmp, r }: { cmp: Comparison; r: CompareRow }) {
  const res = cmp.resolution_s;
  const line = (label: string, a: string, b: string) => (
    <tr key={label}><th>{label}</th><td>{a}</td><td>{b}</td></tr>
  );
  const m = (x: Metrics | null, f: (v: Metrics) => string) => (x ? f(x) : "—");
  return (
    <>
      <div className="evidence-head">
        <span className="eyebrow">D{r.index} · {r.name_a ?? r.name_b}</span>
        <Pill tone={TONE[r.verdict]}>{cmp.verdict_labels[r.verdict]}</Pill>
      </div>
      {r.reasons.length > 0 ? (
        <ul className="reason-list" data-test="reasons">
          {r.reasons.map((x, i) => <li key={i} className={x.severity}>{x.text}</li>)}
        </ul>
      ) : <p className="body-text">Nothing beyond tolerance on this channel.</p>}
      <table className="ab-table">
        <thead><tr><th></th><th className="a">A · baseline</th><th className="b">B · new</th></tr></thead>
        <tbody>
          {line("Δt", m(r.a, (v) => v.dt === null ? cmp.status_labels[v.status] : measuredText(v.dt, res)),
                      m(r.b, (v) => v.dt === null ? cmp.status_labels[v.status] : measuredText(v.dt, res)))}
          {line("Edge", m(r.a, (v) => v.edge ?? "—"), m(r.b, (v) => v.edge ?? "—"))}
          {line("Level at ref", m(r.a, (v) => String(v.level_at_ref ?? "—")), m(r.b, (v) => String(v.level_at_ref ?? "—")))}
          {line("First pulse", m(r.a, (v) => measuredText(v.first_pulse, res)), m(r.b, (v) => measuredText(v.first_pulse, res)))}
          {line("Edges in span", m(r.a, (v) => String(v.edges ?? "—")), m(r.b, (v) => String(v.edges ?? "—")))}
          {line("Time high", m(r.a, (v) => pct(v.duty)), m(r.b, (v) => pct(v.duty)))}
          {line("Shortest pulse", m(r.a, (v) => measuredText(v.min_pulse, res)), m(r.b, (v) => measuredText(v.min_pulse, res)))}
        </tbody>
      </table>
    </>
  );
}
