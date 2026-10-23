// Thin fetch wrappers.  Errors become one readable sentence (the server's
// `detail`), never a stack trace in the UI.
import { API } from "./constants";
import type { AppStatus, FileEntry, Result, Settings, Wave } from "./types";

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(API(path), {
      ...init,
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError("Edge Bench's server is not answering. Restart the app from its shortcut.");
  }
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      if (body?.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* not JSON */ }
    throw new ApiError(msg);
  }
  return r.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  call<T>(path, { method: "POST", body: JSON.stringify(body), signal });

export const api = {
  status: () => call<AppStatus>("status"),
  files: () => call<{ files: FileEntry[] }>("files"),
  add: (paths: string[]) => post<{ added: FileEntry[] }>("files", { paths }),
  remove: (id: number) => call<{ ok: boolean }>(`files/${id}`, { method: "DELETE" }),
  retry: (id: number) => post<FileEntry>(`files/${id}/retry`, {}),
  measure: (file_id: number, settings: Settings, signal?: AbortSignal) =>
    post<Result>("measure", { file_id, settings }, signal),
  waveform: (file_id: number, t0: number, t1: number, px: number, signal?: AbortSignal) =>
    call<Wave>(`waveform?file_id=${file_id}&t0=${t0}&t1=${t1}&px=${Math.round(px)}`, { signal }),
  dialog: (kind: "files" | "folder" | "save" | "exe", initial?: string, name?: string) =>
    post<{ paths: string[] }>("dialog", { kind, initial, name }),
  export: (file_ids: number[], settings: Settings, unit: string, path: string) =>
    post<{ path: string; captures: number; skipped: string[] }>("export", { file_ids, settings, unit, path }),
  reveal: (path: string) => post<{ ok: boolean }>("reveal", { path }),
  setLogic2: (path: string | null) => post<AppStatus["logic2"]>("settings/logic2", { path }),
  quit: () => post<{ ok: boolean }>("quit", {}),
  clearCache: () => post<{ cache_bytes: number }>("cache/clear", {}),
};
