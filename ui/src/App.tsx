// The instrument shell: header · captures rail · waveform + results · inspector · status bar.
// The page never scrolls; only the rail list, the inspector and the table do.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "./api";
import { Inspector, logic2Text } from "./components/Inspector";
import { Rail } from "./components/Rail";
import { Results } from "./components/Results";
import { Waveform, type View } from "./components/Waveform";
import { Icon, Segmented } from "./components/ui";
import { C } from "./constants";
import { bytes, measuredText, rate, timeText } from "./format";
import { loadForm, saveForm, toSettings, type Form } from "./settings";
import { applyTheme, initialTheme, THEMES, type ThemeName } from "./theme";
import type { AppStatus, FileEntry, Result } from "./types";

interface Toast { id: number; kind: "ok" | "error"; text: string; action?: { label: string; run: () => void } }

function focusView(r: Result | null, begin: number, end: number): View {
  const full = { t0: begin, t1: end > begin ? end : begin + 1e-3 };
  const ref = r?.reference;
  if (!ref) return full;
  const ts = r!.channels.filter((c) => c.status === "ok" && c.time !== null).map((c) => c.time!);
  const hi = Math.max(ref.time, ...ts);
  let span = hi - ref.time;
  const minSpan = Math.max((r!.resolution_s ?? 1e-8) * 40, 1e-6);
  if (span < minSpan) span = minSpan * 10;
  const pad = span * (C.waveform.default_span_factor - 1);
  return { t0: ref.time - pad, t1: Math.max(hi, ref.time + span) + pad };
}

export default function App() {
  const [theme, setTheme] = useState<ThemeName>(initialTheme);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [selectedFile, setSelectedFile] = useState<number | null>(null);
  const [form, setFormState] = useState<Form>(loadForm);
  const [result, setResult] = useState<Result | null>(null);
  const [measureError, setMeasureError] = useState<string | null>(null);
  const [selectedCh, setSelectedCh] = useState<number | null>(null);
  const [view, setView] = useState<View>({ t0: 0, t1: 1 });
  const [status, setStatus] = useState<AppStatus | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [busy, setBusy] = useState(false);
  const [scope, setScope] = useState<"one" | "all">("one");
  const [quit, setQuit] = useState(false);
  const toastId = useRef(0);

  useEffect(() => applyTheme(theme), [theme]);
  const setForm = (f: Form) => { setFormState(f); saveForm(f); };

  const toast = useCallback((kind: Toast["kind"], text: string, action?: Toast["action"]) => {
    const id = ++toastId.current;
    setToasts((t) => [...t.slice(-3), { id, kind, text, action }]);
    if (kind === "ok") window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), C.ui.toast_ms);
  }, []);
  const fail = useCallback((e: unknown) => toast("error", e instanceof ApiError ? e.message : String(e)), [toast]);

  // ---- files: poll while anything is loading --------------------------------
  const refreshFiles = useCallback(async () => {
    try {
      const { files } = await api.files();
      setFiles(files);
      return files;
    } catch (e) { fail(e); return null; }
  }, [fail]);

  const loading = files.some((f) => f.status === "queued" || f.status === "loading");
  useEffect(() => {
    if (!loading) return;
    const t = window.setInterval(refreshFiles, C.ui.poll_ms);
    return () => window.clearInterval(t);
  }, [loading, refreshFiles]);

  const refreshStatus = useCallback(() => { api.status().then(setStatus).catch(() => {}); }, []);
  useEffect(() => { refreshFiles(); refreshStatus(); }, [refreshFiles, refreshStatus]);
  useEffect(() => { if (!loading) refreshStatus(); }, [loading, refreshStatus]);

  const file = files.find((f) => f.id === selectedFile) ?? null;
  const capture = file?.status === "ready" ? file.capture ?? null : null;
  const readyFiles = files.filter((f) => f.status === "ready");

  // keep a valid selection
  useEffect(() => {
    if (selectedFile === null || !files.some((f) => f.id === selectedFile)) {
      setSelectedFile(files.length ? files[files.length - 1].id : null);
    }
  }, [files, selectedFile]);

  // a reference channel that exists in this capture
  useEffect(() => {
    if (!capture) return;
    if (form.refChannel === null || !capture.channels.some((c) => c.index === form.refChannel)) {
      setForm({ ...form, refChannel: capture.channels[0]?.index ?? null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [capture]);

  const { settings, errors } = useMemo(() => toSettings(form), [form]);
  const settingsKey = JSON.stringify(settings);

  // ---- measure on every change (debounced, cancellable) ---------------------
  useEffect(() => {
    if (!capture || !file || !settings) { setResult(null); return; }
    setResult((r) => (r && r.file === file.path ? r : null));     // never show another file's numbers
    const ctl = new AbortController();
    const t = window.setTimeout(() => {
      api.measure(file.id, settings, ctl.signal)
        .then((r) => { setResult(r); setMeasureError(null); })
        .catch((e) => {
          if ((e as Error).name === "AbortError") return;
          setResult(null);
          setMeasureError(e.message);
        });
    }, 120);
    return () => { window.clearTimeout(t); ctl.abort(); };
  }, [capture, file?.id, settingsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // reset the view when the measured event moves (not on every redraw)
  const focusKey = result ? `${file?.id}|${result.reference?.time}|${result.channels.map((c) => c.time).join(",")}` : `${file?.id}`;
  useEffect(() => {
    if (capture) setView(focusView(result, capture.begin, capture.end));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusKey, !!capture]);

  // ---- actions -------------------------------------------------------------
  const add = async (paths: string[]) => {
    if (!paths.length) return;
    try {
      const { added } = await api.add(paths);
      await refreshFiles();
      if (added.length) { setSelectedFile(added[added.length - 1].id); setSelectedCh(null); }
      const bad = added.filter((a) => a.status === "error");
      if (bad.length) toast("error", `${bad[0].name}: ${bad[0].error}`);
    } catch (e) { fail(e); }
  };
  const dialogAdd = async (kind: "files" | "folder") => {
    setBusy(true);
    try {
      const { paths } = await api.dialog(kind, file ? dirname(file.path) : undefined);
      await add(paths);
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const exportExcel = async () => {
    if (!settings) { toast("error", "Fix the highlighted settings first."); return; }
    const ids = scope === "all" && readyFiles.length > 1 ? readyFiles.map((f) => f.id) : file ? [file.id] : [];
    if (!ids.length) { toast("error", "Nothing loaded to export yet."); return; }
    const first = files.find((f) => f.id === ids[0])!;
    const suggested = ids.length > 1 ? C.excel.batch_filename : stem(first.name) + C.excel.filename_suffix;
    setBusy(true);
    try {
      const { paths } = await api.dialog("save", dirname(first.path), suggested);
      if (!paths.length) return;
      const r = await api.export(ids, settings, form.exportUnit, paths[0]);
      toast("ok", `Saved ${basename(r.path)} · ${r.captures} capture${r.captures > 1 ? "s" : ""}` +
        (r.skipped.length ? ` · skipped ${r.skipped.length} not loaded` : ""),
        { label: "Show in folder", run: () => { api.reveal(r.path).catch(fail); } });
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const remove = async (id: number) => { await api.remove(id).catch(fail); refreshFiles(); };
  const retry = async (id: number) => { await api.retry(id).catch(fail); refreshFiles(); };

  // ---- keyboard --------------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el.closest("input, select, textarea") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Escape") { setSelectedCh(null); setToasts([]); return; }
      if (!capture) return;
      const span = view.t1 - view.t0;
      const chans = capture.channels.map((c) => c.index);
      if (e.key === "f" || e.key === "F") setView({ t0: capture.begin, t1: capture.end });
      else if (e.key === "r" || e.key === "R") setView(focusView(result, capture.begin, capture.end));
      else if (e.key === "+" || e.key === "=") setView({ t0: view.t0 + span * 0.1, t1: view.t1 - span * 0.1 });
      else if (e.key === "-") setView({ t0: view.t0 - span * 0.125, t1: view.t1 + span * 0.125 });
      else if (e.key === "ArrowLeft") setView({ t0: view.t0 - span * 0.1, t1: view.t1 - span * 0.1 });
      else if (e.key === "ArrowRight") setView({ t0: view.t0 + span * 0.1, t1: view.t1 + span * 0.1 });
      else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (el.closest("tr")) return;
        const i = selectedCh === null ? -1 : chans.indexOf(selectedCh);
        const j = e.key === "ArrowDown" ? Math.min(chans.length - 1, i + 1) : Math.max(0, i - 1);
        setSelectedCh(chans[j]);
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [capture, view, result, selectedCh]);

  const otherTheme: ThemeName = theme === "midnight" ? "daylight" : "midnight";

  if (quit) return (
    <div className="viewport empty-state" style={{ height: "100dvh" }}>
      <div className="state-card"><img src="/favicon.svg" alt="" width={52} height={52} />
        <h2>{C.app.name} has stopped</h2><p>You can close this tab. Start it again from its shortcut.</p></div>
    </div>
  );

  return (
    <div className={`app ${result ? "" : "no-results"}`}>
      <header className="app-header">
        <div className="brand">
          <img src="/favicon.svg" alt="" width={24} height={24} />
          <span>{C.app.name}</span>
        </div>
        <div className="header-divider" />
        <div className="doc" title={file?.path}>
          <span className="doc-name" data-test="doc-name">{file ? file.name : "No capture open"}</span>
          {capture && <span className="doc-meta">{capture.device ?? capture.source.toUpperCase()} · {rate(capture.sample_rate)} · {capture.channels.length} channels · {timeText(capture.end - capture.begin)}</span>}
        </div>
        <div className="header-spacer" />
        <button className="icon-btn big" onClick={() => setTheme(otherTheme)} data-test="theme-toggle"
          aria-label={`Switch to ${THEMES[otherTheme].label} theme`} title={`${THEMES[otherTheme].label} theme`}>
          <Icon name={theme === "midnight" ? "sun" : "moon"} size={16} />
        </button>
        {readyFiles.length > 1 && (
          <Segmented label="Export scope" value={scope} onChange={setScope} testId="export-scope"
            options={[{ value: "one", label: "This capture" }, { value: "all", label: `All ${readyFiles.length}` }]} />
        )}
        <button className="primary-btn" onClick={exportExcel} disabled={busy || !result || !settings} data-test="export">
          <Icon name="download" /> Export Excel
        </button>
      </header>

      <Rail files={files} selected={selectedFile} onSelect={(id) => { setSelectedFile(id); setSelectedCh(null); }}
        onAddFiles={() => dialogAdd("files")} onAddFolder={() => dialogAdd("folder")}
        onAddPath={(p) => add([p])} onRemove={remove} onRetry={retry} busy={busy} />

      <main className="workspace">
        <div className="toolbar">
          <span className="toolbar-title" data-test="ref-summary">{result?.reference
            ? <>Reference <b className="ref-text">{result.reference.name} {result.reference.edge === "rising" ? "↑" : "↓"}</b> #{result.reference.occurrence} at {measuredText(result.reference.time, result.resolution_s)}</>
            : capture ? (measureError ?? (settings ? "Measuring…" : "Fix the highlighted settings")) : "Waveform"}</span>
          <div className="toolbar-spacer" />
          {capture && (
            <span className="toolbar-caption" data-test="view-caption">
              {result?.reference ? "Axis: time from reference" : "Axis: time in capture"} · {timeText(view.t1 - view.t0)} shown
            </span>
          )}
          {capture && (
            <>
              <button className="ghost-btn" onClick={() => setView(focusView(result, capture.begin, capture.end))} title="R" data-test="view-measure">
                <Icon name="target" /> Measurement
              </button>
              <button className="ghost-btn" onClick={() => setView({ t0: capture.begin, t1: capture.end })} title="F" data-test="view-all">
                <Icon name="fit" /> Whole capture
              </button>
            </>
          )}
        </div>

        {capture && file ? (
          <Waveform fileId={file.id} capture={capture} result={result} view={view} setView={setView}
            selected={selectedCh} onSelect={setSelectedCh} excluded={form.excluded}
            windowS={settings?.window_s ?? null} theme={theme} />
        ) : (
          <div className="viewport empty-state" data-test="empty">
            {file && (file.status === "loading" || file.status === "queued") ? (
              <div className="state-card">
                <div className="spinner" aria-hidden="true" />
                <h2>{file.step || "Waiting"}</h2>
                <p>{file.name}{file.kind === "sal" ? " — Logic 2 exports it once; reopening it later is instant." : ""}</p>
              </div>
            ) : file && file.status === "error" ? (
              <div className="state-card error">
                <h2>Could not open {file.name}</h2>
                <p>{file.error}</p>
                <div className="btn-row center"><button className="ghost-btn" onClick={() => retry(file.id)}><Icon name="retry" /> Retry</button></div>
              </div>
            ) : (
              <div className="state-card">
                <img src="/favicon.svg" alt="" width={52} height={52} />
                <h2>Open a Saleae capture</h2>
                <p>Pick <b>.sal</b> files (Logic 2 opens them in the background), a Logic 2 <b>digital CSV</b>, or a <b>binary export</b> folder. Choose a reference edge; every other channel gets its Δt, ready for Excel.</p>
                <div className="btn-row center">
                  <button className="primary-btn" onClick={() => dialogAdd("files")} disabled={busy} data-test="empty-add"><Icon name="plus" /> Add captures…</button>
                  <button className="ghost-btn" onClick={() => dialogAdd("folder")} disabled={busy}><Icon name="folder" /> Add a folder…</button>
                </div>
                <p className="small">Logic 2: {logic2Text(status)}</p>
              </div>
            )}
          </div>
        )}

        {result && <Results result={result} selected={selectedCh} onSelect={setSelectedCh} />}
      </main>

      <Inspector form={form} setForm={setForm} errors={capture ? errors : {}} capture={capture} result={result} status={status}
        onBrowseLogic2={async () => {
          try {
            const { paths } = await api.dialog("exe");
            if (paths.length) { await api.setLogic2(paths[0]); refreshStatus(); toast("ok", "Logic 2 path saved."); }
          } catch (e) { fail(e); }
        }}
        onResetLogic2={async () => { await api.setLogic2(null).catch(fail); refreshStatus(); }}
        onQuit={async () => {
          try { await api.quit(); setQuit(true); } catch (e) { fail(e); }
        }}
        onClearCache={async () => {
          try { const r = await api.clearCache(); toast("ok", `Cache cleared (${bytes(r.cache_bytes)} left).`); refreshFiles(); refreshStatus(); }
          catch (e) { fail(e); }
        }} />

      <footer className="status-bar">
        <span data-test="status-left">
          {capture ? `${capture.channels.length} ch · ${capture.channels.reduce((a, c) => a + c.edges, 0).toLocaleString()} edges · resolution ${capture.resolution_s ? "±" + timeText(capture.resolution_s) : "unknown"}` : `${files.length} capture${files.length === 1 ? "" : "s"}`}
          {result ? ` · measured in ${timeText(result.compute_s)}` : ""}
        </span>
        <span className="status-mid">Logic 2: {logic2Text(status)}</span>
        <span className="keys"><kbd className="status-key">R</kbd> measurement <kbd className="status-key">F</kbd> whole <kbd className="status-key">+</kbd><kbd className="status-key">−</kbd> zoom <kbd className="status-key">↑↓</kbd> channel · scroll zoom · drag pan</span>
      </footer>

      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`} role={t.kind === "error" ? "alert" : "status"} data-test={`toast-${t.kind}`}>
            <span>{t.text}</span>
            {t.action && <button className="link-btn" onClick={t.action.run}>{t.action.label}</button>}
            <button className="icon-btn" aria-label="Dismiss" onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}><Icon name="x" size={12} /></button>
          </div>
        ))}
      </div>
    </div>
  );
}

const dirname = (p: string) => p.replace(/[\\/][^\\/]*$/, "");
const basename = (p: string) => p.split(/[\\/]/).pop() ?? p;
const stem = (n: string) => n.replace(/\.[^.]+$/, "");
