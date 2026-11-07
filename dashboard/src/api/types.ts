// Shapes of the engine's JSON API (api version 1). Times are ISO-8601 UTC strings.

/** "connecting": no frame yet and the first attempt failed (e.g. stream unreachable). */
export type SourceState =
  | "starting"
  | "connecting"
  | "running"
  | "reconnecting"
  | "ended"
  | "error";

export interface Status {
  source_state: SourceState;
  fps: number;
  frames_processed: number;
  trucks_now: number;
  trucks_total: number;
  started_at: string;
  telemetry_state: string;
}

export type ClipStatus = "pending" | "saved" | "skipped" | "failed" | "removed";

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
  /** Absent on engines that predate clip status. */
  clip_status?: ClipStatus;
}

export interface Runtime {
  confidence: number;
  sahi_enabled: boolean;
  /** Alert channel name -> switched on. Absent on engines without alerts. */
  alerts?: Record<string, boolean>;
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
  /** Configured alert channels (desktop, telegram, mqtt); may be empty or absent. */
  alerts?: string[];
}

export type LiveMessage =
  | { type: "status"; status: Status }
  | { type: "truck.new" | "truck.updated"; truck: Truck };
