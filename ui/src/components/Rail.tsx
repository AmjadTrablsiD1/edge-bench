// Left rail: the captures.  Add by native dialog (files or folder) or by
// pasting a path; each row shows its own load progress or the reason it failed.
import { useState } from "react";
import { num, rate } from "../format";
import type { FileEntry } from "../types";
import { Icon, Pill } from "./ui";

interface Props {
  files: FileEntry[];
  selected: number | null;
  onSelect: (id: number) => void;
  onAddFiles: () => void;
  onAddFolder: () => void;
  onAddPath: (p: string) => void;
  onRemove: (id: number) => void;
  onRetry: (id: number) => void;
  busy: boolean;
}

function detail(f: FileEntry): string {
  if (f.status === "error") return f.error ?? "Failed";
  if (f.status !== "ready") return f.step || (f.status === "queued" ? "Waiting" : "Loading");
  const c = f.capture!;
  const parts = [c.device ?? c.source.toUpperCase(), rate(c.sample_rate), `${c.channels.length} ch`];
  if (f.load_s !== null && f.load_s > 0.05) parts.push(`${num(f.load_s)} s`);
  return parts.join(" · ");
}

export function Rail(p: Props) {
  const [path, setPath] = useState("");
  return (
    <aside className="rail" aria-label="Captures">
      <div className="eyebrow">Captures <span className="count">{p.files.length}</span></div>
      <div className="rail-actions">
        <button className="ghost-btn" onClick={p.onAddFiles} disabled={p.busy} data-test="add-files">
          <Icon name="plus" /> Add files…
        </button>
        <button className="ghost-btn" onClick={p.onAddFolder} disabled={p.busy} data-test="add-folder" title="Every .sal and .csv in a folder">
          <Icon name="folder" /> Folder…
        </button>
      </div>
      <form className="path-form" onSubmit={(e) => { e.preventDefault(); if (path.trim()) { p.onAddPath(path); setPath(""); } }}>
        <input type="text" placeholder="…or paste a path and press Enter" value={path} aria-label="Capture path"
          spellCheck={false} onChange={(e) => setPath(e.target.value)} data-test="path-input" />
      </form>
      <ul className="file-list" data-test="file-list">
        {p.files.map((f) => (
          <li key={f.id}>
            <button className={`file-item ${p.selected === f.id ? "active" : ""} st-${f.status}`}
              onClick={() => p.onSelect(f.id)} title={f.path} data-test="file-item">
              <span className="file-name"><Icon name="file" size={12} /> {f.name}</span>
              <span className="file-detail">
                {f.status === "loading" || f.status === "queued" ? <Pill tone="busy">{detail(f)}</Pill>
                  : f.status === "error" ? <span className="file-error">{detail(f)}</span>
                  : detail(f)}
              </span>
            </button>
            <div className="file-tools">
              {f.status === "error" && (
                <button className="icon-btn" aria-label={`Retry ${f.name}`} title="Retry" onClick={() => p.onRetry(f.id)}>
                  <Icon name="retry" size={12} />
                </button>
              )}
              <button className="icon-btn" aria-label={`Remove ${f.name}`} title="Remove from list" onClick={() => p.onRemove(f.id)}>
                <Icon name="x" size={12} />
              </button>
            </div>
          </li>
        ))}
      </ul>
    </aside>
  );
}
