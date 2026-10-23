// Right inspector: the measurement set-up, what was and was not evaluated,
// and the app's own settings (Logic 2 path, cache).
import { useState } from "react";
import { C } from "../constants";
import { bytes, timeText } from "../format";
import type { FormErrors, Form } from "../settings";
import type { AppStatus, CaptureSummary, EdgeKind, Result } from "../types";
import { Field, Icon, Segmented, TimeInput } from "./ui";

type Tab = "setup" | "coverage" | "app";

interface Props {
  form: Form;
  setForm: (f: Form) => void;
  errors: FormErrors;
  capture: CaptureSummary | null;
  result: Result | null;
  status: AppStatus | null;
  onBrowseLogic2: () => void;
  onResetLogic2: () => void;
  onClearCache: () => void;
  onQuit: () => void;
}

const EDGE_OPTS = (any: boolean) => [
  { value: "rising" as EdgeKind, label: "↑ Rising" },
  { value: "falling" as EdgeKind, label: "↓ Falling" },
  ...(any ? [{ value: "any" as EdgeKind, label: "Either" }] : []),
];

export function Inspector(p: Props) {
  const [tab, setTab] = useState<Tab>("setup");
  const f = p.form;
  const set = (patch: Partial<Form>) => p.setForm({ ...f, ...patch });
  const chans = p.capture?.channels ?? [];

  return (
    <aside className="inspector" aria-label="Inspector">
      <div className="inspector-tabs" role="tablist">
        {([["setup", "Measurement"], ["coverage", "Coverage"], ["app", "App"]] as [Tab, string][]).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""}
            onClick={() => setTab(k)} data-test={`tab-${k}`}>{label}</button>
        ))}
      </div>

      <div className="inspector-scroll" role="tabpanel">
        {tab === "setup" && (
          <>
            <section className="well">
              <h3 className="well-cap">Reference</h3>
              <Field label="Reference is">
                <Segmented label="Reference mode" value={f.refMode} testId="ref-mode"
                  onChange={(v) => set({ refMode: v })}
                  options={[{ value: "manual", label: "A chosen channel" },
                            { value: "auto", label: "First to change", title: "The earliest edge on any included channel" }]} />
              </Field>
              {f.refMode === "manual" && (
                <Field label="Channel" htmlFor="ref-ch" error={p.errors.refChannel}>
                  <select id="ref-ch" value={f.refChannel ?? ""} data-test="ref-channel"
                    onChange={(e) => set({ refChannel: e.target.value === "" ? null : Number(e.target.value) })}>
                    {f.refChannel === null && <option value="">Choose…</option>}
                    {chans.map((c) => <option key={c.index} value={c.index}>D{c.index} · {c.name}</option>)}
                  </select>
                </Field>
              )}
              <Field label="Edge">
                <Segmented label="Reference edge" value={f.refEdge} options={EDGE_OPTS(true)} testId="ref-edge"
                  onChange={(v) => set({ refEdge: v })} />
              </Field>
              <div className="field-row">
                {f.refMode === "manual" && (
                  <Field label="Occurrence" htmlFor="occ" error={p.errors.occurrence} hint="1 = first edge">
                    <input id="occ" type="text" inputMode="numeric" value={f.occurrence} data-test="occurrence"
                      onChange={(e) => set({ occurrence: e.target.value })} />
                  </Field>
                )}
                <Field label="Search from" htmlFor="from" error={p.errors.searchFrom} hint="empty = capture start">
                  <TimeInput id="from" value={f.searchFrom} onChange={(q) => set({ searchFrom: q })} placeholder="start" />
                </Field>
              </div>
            </section>

            <section className="well">
              <h3 className="well-cap">Other channels</h3>
              <Field label="Edge to find" hint="the first such edge at or after the reference">
                <Segmented label="Target edge" value={f.targetEdge} options={EDGE_OPTS(true)} testId="target-edge"
                  onChange={(v) => set({ targetEdge: v })} />
              </Field>
              <div className="field-row">
                <Field label="Window" htmlFor="win" error={p.errors.window} hint="empty = no limit">
                  <TimeInput id="win" value={f.window} onChange={(q) => set({ window: q })} placeholder="none" testId="window" />
                </Field>
                <Field label="Glitch filter" htmlFor="gl" error={p.errors.glitch} hint="ignore shorter pulses">
                  <TimeInput id="gl" value={f.glitch} onChange={(q) => set({ glitch: q })} placeholder="off" testId="glitch" />
                </Field>
              </div>
              <Field label={`Channels included (${chans.length - f.excluded.filter((i) => chans.some((c) => c.index === i)).length}/${chans.length})`}>
                <div className="chip-grid" data-test="channel-chips">
                  {chans.map((c) => {
                    const on = !f.excluded.includes(c.index);
                    return (
                      <button key={c.index} type="button" className={`chip ${on ? "on" : ""}`} aria-pressed={on}
                        title={c.name}
                        onClick={() => set({ excluded: on ? [...f.excluded, c.index] : f.excluded.filter((i) => i !== c.index) })}>
                        D{c.index}
                      </button>
                    );
                  })}
                </div>
                {chans.length > 0 && (
                  <div className="chip-actions">
                    <button type="button" className="link-btn" onClick={() => set({ excluded: [] })}>All</button>
                    <button type="button" className="link-btn" onClick={() => set({ excluded: chans.map((c) => c.index) })}>None</button>
                  </div>
                )}
              </Field>
            </section>

            <section className="well">
              <h3 className="well-cap">Excel</h3>
              <Field label="Time unit in the workbook">
                <Segmented label="Excel time unit" value={f.exportUnit} testId="export-unit"
                  onChange={(v) => set({ exportUnit: v })}
                  options={C.units.time.map((u) => ({ value: u.label, label: u.label }))} />
              </Field>
            </section>
          </>
        )}

        {tab === "coverage" && <Coverage result={p.result} capture={p.capture} form={f} />}

        {tab === "app" && (
          <>
            <section className="well">
              <h3 className="well-cap">Logic 2</h3>
              <dl className="evidence">
                <dt>Status</dt><dd data-test="logic2-status">{logic2Text(p.status)}</dd>
                <dt>Executable</dt><dd className="path">{p.status?.logic2.executable ?? "not found"}</dd>
              </dl>
              <p className="body-text">.sal captures are opened by Logic 2 in the background and exported once as binary.
                CSV and binary exports do not need Logic 2.</p>
              <div className="btn-row">
                <button className="ghost-btn" onClick={p.onBrowseLogic2}><Icon name="folder" /> Choose Logic 2…</button>
                {p.status?.logic2.configured && <button className="ghost-btn" onClick={p.onResetLogic2}>Auto-detect</button>}
              </div>
            </section>
            <section className="well">
              <h3 className="well-cap">Export cache</h3>
              <dl className="evidence">
                <dt>Size</dt><dd>{p.status ? `${bytes(p.status.cache_bytes)} of ${p.status.cache_max_gb} GB` : "—"}</dd>
                <dt>Folder</dt><dd className="path">{p.status?.cache_dir ?? "—"}</dd>
              </dl>
              <p className="body-text">Each .sal is exported once; reopening it is instant. Oldest entries are removed above the limit.</p>
              <div className="btn-row"><button className="ghost-btn" onClick={p.onClearCache}>Clear cache</button></div>
            </section>
            <section className="well">
              <h3 className="well-cap">About</h3>
              <p className="body-text">{C.app.name} {C.app.version} — {C.app.tagline}.</p>
              <p className="body-text">Closing the browser tab leaves Edge Bench running in the background, ready to reopen. Quit stops it, and the Logic 2 it started.</p>
              <div className="btn-row"><button className="ghost-btn" onClick={p.onQuit} data-test="quit">Quit Edge Bench</button></div>
            </section>
          </>
        )}
      </div>
    </aside>
  );
}

export function logic2Text(s: AppStatus | null): string {
  if (!s) return "—";
  const l = s.logic2;
  if (l.mode === "own") return `Running (started by Edge Bench) · ${l.version ?? ""} · port ${l.port}`;
  if (l.mode === "user") return `Using your open Logic 2 · ${l.version ?? ""} · port ${l.port}`;
  if (!l.executable) return "Not found — choose Logic.exe below";
  return "Ready (starts when a .sal is opened)";
}

function Coverage(p: { result: Result | null; capture: CaptureSummary | null; form: Form }) {
  const r = p.result;
  return (
    <>
      <section className="well">
        <h3 className="well-cap">Evaluated</h3>
        <ul className="cover-list ok">
          <li>One reference edge: {r?.reference ? `${r.reference.name} ${r.reference.edge} #${r.reference.occurrence}` : "none found"}.</li>
          <li>On every other included channel, the first {p.form.targetEdge === "any" ? "edge (either direction)" : `${p.form.targetEdge} edge`} at or after it. An edge in the same sample counts as Δt = 0.</li>
          <li>Window: {r?.settings.window_s ? `edges later than ${timeText(r.settings.window_s)} are reported as outside` : "none"}.</li>
          <li>Glitch filter: {r?.settings.glitch_s ? `pulses shorter than ${timeText(r.settings.glitch_s)} ignored; the edge reported is where the level settled (as Logic 2's own filter shows it)` : "off — every edge counts"}.</li>
          <li>Resolution: {r?.resolution_s ? `±${timeText(r.resolution_s)} on each Δt (one sample at ${(1 / r.resolution_s / 1e6).toPrecision(3)} MS/s)` : "unknown — the file does not carry the sample rate"}.</li>
        </ul>
      </section>
      <section className="well">
        <h3 className="well-cap">Not evaluated</h3>
        <ul className="cover-list no">
          <li>Edges <em>before</em> the reference (a negative Δt). A channel that switched earlier shows its next edge.</li>
          <li>Repeated cycles — only one reference event per capture. Statistics over every cycle is the next planned mode.</li>
          <li>Analog channels and protocol analyzers in the capture.</li>
          <li>Threshold or propagation effects: times are where Logic 2 sampled the logic level.</li>
        </ul>
      </section>
      {(r?.notes.length ?? 0) > 0 && (
        <section className="well">
          <h3 className="well-cap">Notes on this capture</h3>
          <ul className="cover-list">{r!.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </section>
      )}
    </>
  );
}
