export type EdgeKind = "rising" | "falling" | "any";

export interface ChannelSummary { index: number; name: string; edges: number }

export interface CaptureSummary {
  source: "sal" | "binary" | "csv";
  device: string | null;
  sample_rate: number | null;
  resolution_s: number | null;
  format_version: number | null;
  begin: number;
  end: number;
  channels: ChannelSummary[];
  notes: string[];
}

export interface FileEntry {
  id: number;
  path: string;
  name: string;
  kind: string;
  status: "queued" | "loading" | "ready" | "error";
  step: string;
  error: string | null;
  meta: { device?: string; sample_rate?: number; digital?: number[]; names?: Record<string, string> };
  load_s: number | null;
  capture?: CaptureSummary;
}

export interface Settings {
  ref_mode: "manual" | "auto";
  ref_channel: number | null;
  ref_edge: EdgeKind;
  occurrence: number;
  search_from_s: number | null;
  target_edge: EdgeKind;
  window_s: number | null;
  glitch_s: number;
  excluded: number[];
}

export interface Reference {
  channel: number; name: string; edge: "rising" | "falling";
  time: number; first_cross: number; occurrence: number;
}

export interface ChannelResult {
  index: number; name: string;
  status: "ok" | "no_edge" | "out_of_window" | "reference" | "excluded" | "no_data";
  edge: "rising" | "falling" | null;
  time: number | null; dt: number | null; bounce: number | null; next_dt: number | null;
  level_at_ref: number | null; edges_total: number;
}

export interface Result {
  file: string;
  reference: Reference | null;
  channels: ChannelResult[];
  resolution_s: number | null;
  settings: Settings;
  notes: string[];
  status_labels: Record<string, string>;
  compute_s: number;
}

export interface WaveSegment {
  mode: "exact" | "buckets";
  begin: number; end: number;
  level?: number; times?: number[];
  levels?: number[]; counts?: number[];
}
export interface WaveChannel { index: number; name: string; segments: WaveSegment[] }
export interface Wave { t0: number; t1: number; px: number; channels: WaveChannel[] }

export interface AppStatus {
  logic2: { mode: string; port: number | null; version: string | null; executable: string | null;
            configured: string | null; user_port_open: boolean };
  platform: string; cache_bytes: number; cache_dir: string; cache_max_gb: number;
}
