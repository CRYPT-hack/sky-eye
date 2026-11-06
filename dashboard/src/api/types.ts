// Shapes of the engine's JSON API (api version 1). Times are ISO-8601 UTC strings.

export type SourceState = "starting" | "running" | "ended" | "error";

export interface Status {
  source_state: SourceState;
  fps: number;
  frames_processed: number;
  trucks_now: number;
  trucks_total: number;
  started_at: string;
  telemetry_state: string;
}

export interface Truck {
  id: number;
  run_id: number;
  track_id: number;
  first_seen: string;
  last_seen: string;
  best_score: number;
  snapshot_url: string | null;
  crop_url: string | null;
  clip_url: string | null;
  lat: number | null;
  lon: number | null;
  geo_error_m: number | null;
  geo_status: string;
}

export interface Runtime {
  confidence: number;
  sahi_enabled: boolean;
}

export interface Info {
  version: string;
  source: { type: "file" | "capture" | "stream" | "go2rtc"; name: string; fps: number };
  detector: {
    model: string;
    input_size: number;
    device: "cuda" | "cpu";
    gpu: string | null;
    classes: string[];
  };
  auth: "none" | "token";
  dashboard: boolean;
}

export type LiveMessage =
  | { type: "status"; status: Status }
  | { type: "truck.new" | "truck.updated"; truck: Truck };
