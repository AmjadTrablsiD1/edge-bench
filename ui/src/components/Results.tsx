// Bottom of the workspace: the results table, and the evidence for the
// selected channel beside it -- every Δt can be traced to the two edges it
// was measured between.
import { useMemo, useState } from "react";
import { measured, measuredText, timeText } from "../format";
import type { ChannelResult, Result } from "../types";
import { Num, Pill } from "./ui";

const TONE: Record<ChannelResult["status"], "ok" | "ref" | "miss" | "muted"> = {
  ok: "ok", reference: "ref", no_edge: "miss", out_of_window: "miss", no_data: "miss", excluded: "muted",
};

type SortKey = "index" | "dt";

export function Results(p: { result: Result; selected: number | null; onSelect: (i: number) => void }) {
  const [sort, setSort] = useState<SortKey>("index");
  const rows = useMemo(() => {
    const r = [...p.result.channels];
    if (sort === "dt") r.sort((a, b) => (a.dt ?? Infinity) - (b.dt ?? Infinity) || a.index - b.index);
    return r;
  }, [p.result, sort]);
  const sel = p.result.channels.find((c) => c.index === p.selected) ?? null;
  const found = p.result.channels.filter((c) => c.status === "ok").length;
  const considered = p.result.channels.filter((c) => c.status !== "excluded" && c.status !== "reference").length;

  return (
    <div className="workspace-bottom">
      <div className="table-wrap" data-test="results">
        <table className="results">
          <thead>
            <tr>
              <th className="sortable" aria-sort={sort === "index" ? "ascending" : "none"} onClick={() => setSort("index")}>Ch</th>
              <th>Name</th>
              <th>Status</th>
              <th>Edge</th>
              <th className="sortable num-col" aria-sort={sort === "dt" ? "ascending" : "none"} onClick={() => setSort("dt")}
                title="Sort by Δt">Δt {sort === "dt" ? "▲" : ""}</th>
              <th className="num-col">Edge at</th>
              <th className="num-col" title="Level when the reference edge happened">Level@ref</th>
              <th className="num-col">Edges</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.index} className={`${p.selected === c.index ? "selected" : ""} st-${c.status}`}
                onClick={() => p.onSelect(c.index)} data-test={`row-${c.index}`} tabIndex={0}
                onKeyDown={(e) => { if (e.key === "Enter") p.onSelect(c.index); }}>
                <td className="mono">D{c.index}</td>
                <td className="name-cell">{c.name}</td>
                <td><Pill tone={TONE[c.status]}>{p.result.status_labels[c.status] ?? c.status}</Pill></td>
                <td>{c.edge ? (c.edge === "rising" ? "↑ rising" : "↓ falling") : "—"}</td>
                <td className="num-col mono strong" data-test={`dt-${c.index}`}>
                  {c.status === "out_of_window" ? <span className="muted">&gt; window</span> : measuredText(c.dt, p.result.resolution_s)}
                </td>
                <td className="num-col mono">{measuredText(c.time, p.result.resolution_s)}</td>
                <td className="num-col mono">{c.level_at_ref ?? "—"}</td>
                <td className="num-col mono">{c.edges_total.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="evidence-panel" data-test="evidence">
        {sel ? <Evidence r={p.result} c={sel} /> : (
          <div className="evidence-empty">
            <div className="eyebrow">Summary</div>
            <div className="hero-value">{found}<small>of {considered} channels switched</small></div>
            <p className="body-text">Click a row or a lane to see how its Δt was measured.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function Evidence({ r, c }: { r: Result; c: ChannelResult }) {
  const ref = r.reference;
  const res = r.resolution_s;
  return (
    <>
      <div className="evidence-head">
        <span className="eyebrow">D{c.index} · {c.name}</span>
        <Pill tone={TONE[c.status]}>{r.status_labels[c.status]}</Pill>
      </div>
      <div className="hero-value" data-test="hero-dt">
        {c.status === "ok" || c.status === "reference" ? <Num v={measured(c.dt, res)} /> : <span className="muted-hero">—</span>}
        {c.status === "ok" ? <span className="pm">{res ? `± ${timeText(res)}` : "resolution unknown"}</span> : null}
      </div>
      <dl className="evidence">
        <dt>Reference</dt>
        <dd>{ref ? `${ref.name} ${ref.edge} #${ref.occurrence} at ${measuredText(ref.time, res)}` : "none"}</dd>
        <dt>This edge</dt>
        <dd>{c.edge ? `${c.edge} at ${measuredText(c.time, res)}` : c.status === "out_of_window" ? `next edge at +${measuredText(c.next_dt, res)}` : "none"}</dd>
        {c.bounce !== null && c.bounce > 0 && (<><dt>Bounce</dt><dd>settled {timeText(c.bounce)} after first crossing</dd></>)}
        <dt>Level at ref</dt><dd>{c.level_at_ref ?? "no data"}</dd>
        <dt>Edges in capture</dt><dd>{c.edges_total.toLocaleString()}</dd>
      </dl>
      <p className="body-text">{explain(c, r)}</p>
    </>
  );
}

function explain(c: ChannelResult, r: Result): string {
  switch (c.status) {
    case "ok": return `This channel changed ${measuredText(c.dt, r.resolution_s)} after the reference edge. Both edges are sampled, so the true delay is within ±1 sample${r.resolution_s ? ` (${timeText(r.resolution_s)})` : ""}.`;
    case "reference": return "This is the reference channel; every Δt is measured from its edge.";
    case "no_edge": return "No matching edge between the reference and the end of the capture. Try 'Either' edge, a later occurrence, or check the probe.";
    case "out_of_window": return `The next edge is ${timeText(c.next_dt)} after the reference, beyond the window. Widen the window if that edge is the one you want.`;
    case "no_data": return "The capture has no data for this channel at the reference time (a gap or disabled channel).";
    default: return "Excluded from the measurement in the channel list.";
  }
}
