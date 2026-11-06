import { vi } from "vitest";
import type { ApiClient } from "../api/client";
import type { LiveOptions, LiveState } from "../api/live";
import type { LiveMessage, Status, Truck } from "../api/types";

export function makeTruck(id: number, over: Partial<Truck> = {}): Truck {
  return {
    id,
    run_id: 1,
    track_id: id,
    first_seen: new Date(Date.UTC(2026, 10, 6, 6, 0, id)).toISOString(),
    last_seen: new Date(Date.UTC(2026, 10, 6, 6, 0, id + 5)).toISOString(),
    best_score: 0.8,
    snapshot_url: `/media/s${id}.jpg`,
    crop_url: `/media/c${id}.jpg`,
    clip_url: null,
    lat: null,
    lon: null,
    geo_error_m: null,
    geo_status: "none",
    ...over,
  };
}

export const STATUS: Status = {
  source_state: "running",
  fps: 25,
  frames_processed: 100,
  trucks_now: 0,
  trucks_total: 0,
  started_at: "2026-11-06T06:00:00Z",
  telemetry_state: "off",
};

export function makeClient(trucks: Truck[] = [], status: Status = STATUS): ApiClient {
  return {
    getInfo: vi.fn(),
    getStatus: vi.fn(async () => status),
    listTrucks: vi.fn(async () => ({ trucks, limit: 20, offset: 0 })),
    getTruck: vi.fn(),
    getRuntime: vi.fn(),
    putRuntime: vi.fn(),
    mediaUrl: (p: string) => `${p}?token=t`,
    streamUrl: () => "/stream.mjpg?token=t",
    liveUrl: () => "ws://x/ws",
  } as unknown as ApiClient;
}

/** A stand-in for connectLive that hands the test the callbacks. */
export function makeConnect() {
  const calls: LiveOptions[] = [];
  const stops: ReturnType<typeof vi.fn>[] = [];
  const connect = (opts: LiveOptions) => {
    calls.push(opts);
    const stop = vi.fn();
    stops.push(stop);
    return stop;
  };
  return {
    connect,
    calls,
    stops,
    last: () => calls[calls.length - 1],
    send: (m: LiveMessage) => calls[calls.length - 1].onMessage(m),
    state: (s: LiveState) => calls[calls.length - 1].onState(s),
  };
}
