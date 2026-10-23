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

export type Verdict = "same" | "changed" | "critical" | "excluded" | "missing";

export interface Metrics {
  status: ChannelResult["status"];
  dt: number | null; edge: "rising" | "falling" | null; level_at_ref: number | null;
  first_pulse: number | null; edges: number | null; duty: number | null; min_pulse: number | null;
}

export interface CompareRow {
  index: number; name_a: string | null; name_b: string | null; verdict: Verdict;
  a: Metrics | null; b: Metrics | null;
  reasons: { metric: string; severity: "changed" | "critical"; text: string }[];
  dt_delta: number | null;
}

export interface Tolerance {
  time_abs_s: number; time_rel: number; pulse_abs_s: number; pulse_rel: number;
  count_abs: number; count_rel: number; duty_abs: number;
}

export interface Comparison {
  file_a: string; file_b: string;
  reference_a: Reference | null; reference_b: Reference | null;
  span_s: number | null; resolution_s: number | null;
  tolerance: Tolerance; settings: Settings;
  rows: CompareRow[];
  summary: Record<Verdict, number>;
  notes: string[];
  verdict_labels: Record<Verdict, string>;
  status_labels: Record<string, string>;
  compute_s: number;
}
