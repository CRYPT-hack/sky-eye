import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { connectLive, type LiveState } from "./live";
import type { LiveMessage } from "./types";

class FakeWS {
  static all: FakeWS[] = [];
  readyState = 0;
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeWS.all.push(this);
  }
  close() {
    this.closed = true;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  message(data: string) {
    this.onmessage?.({ data });
  }
  serverClose(code: number) {
    this.onclose?.({ code });
  }
}

beforeEach(() => {
  FakeWS.all = [];
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

function start(extra: { url?: () => string; backoffMs?: number[] } = {}) {
  const states: LiveState[] = [];
  const msgs: LiveMessage[] = [];
  const stop = connectLive({
    url: extra.url ?? (() => "ws://h/ws"),
    onMessage: (m) => msgs.push(m),
    onState: (s) => states.push(s),
    WebSocketImpl: FakeWS as unknown as typeof WebSocket,
    backoffMs: extra.backoffMs,
  });
  return { states, msgs, stop };
}

test("connects, reports open and delivers messages", () => {
  const { states, msgs } = start();
  expect(states).toEqual(["connecting"]);
  FakeWS.all[0].open();
  expect(states).toEqual(["connecting", "open"]);
  FakeWS.all[0].message(JSON.stringify({ api: 1, type: "truck.new", truck: { id: 1 } }));
  expect(msgs).toEqual([{ api: 1, type: "truck.new", truck: { id: 1 } }]);
});

test("reconnects with backoff", () => {
  const { states } = start();
  FakeWS.all[0].open();
  FakeWS.all[0].serverClose(1006);
  expect(states.at(-1)).toBe("reconnecting");
  vi.advanceTimersByTime(999);
  expect(FakeWS.all).toHaveLength(1);
  vi.advanceTimersByTime(1);
  expect(FakeWS.all).toHaveLength(2);
  FakeWS.all[1].serverClose(1006);
  vi.advanceTimersByTime(1999);
  expect(FakeWS.all).toHaveLength(2);
  vi.advanceTimersByTime(1);
  expect(FakeWS.all).toHaveLength(3);
});

test("backoff repeats last value and resets after open; 1001 reconnects", () => {
  start({ backoffMs: [10, 20] });
  FakeWS.all[0].serverClose(1001);
  vi.advanceTimersByTime(10);
  FakeWS.all[1].serverClose(1006);
  vi.advanceTimersByTime(20);
  FakeWS.all[2].serverClose(1006);
  vi.advanceTimersByTime(20);
  expect(FakeWS.all).toHaveLength(4);
  FakeWS.all[3].open();
  FakeWS.all[3].serverClose(1006);
  vi.advanceTimersByTime(10);
  expect(FakeWS.all).toHaveLength(5);
});

test("re-reads url on reconnect", () => {
  let tok = "a";
  start({ url: () => `ws://h/ws?token=${tok}` });
  tok = "b";
  FakeWS.all[0].serverClose(1006);
  vi.advanceTimersByTime(1000);
  expect(FakeWS.all[1].url).toBe("ws://h/ws?token=b");
});

test("4401 stops reconnecting", () => {
  const { states } = start();
  FakeWS.all[0].serverClose(4401);
  expect(states.at(-1)).toBe("unauthorized");
  vi.advanceTimersByTime(60_000);
  expect(FakeWS.all).toHaveLength(1);
});

test("ignores malformed messages", () => {
  const { msgs } = start();
  FakeWS.all[0].open();
  FakeWS.all[0].message("not json");
  FakeWS.all[0].message(JSON.stringify({ api: 1, type: "weird" }));
  FakeWS.all[0].message(JSON.stringify(null));
  FakeWS.all[0].message(JSON.stringify({ api: 1, type: "truck.new" }));
  expect(msgs).toEqual([]);
});

test("stop closes the socket and silences everything", () => {
  const { states, msgs, stop } = start();
  FakeWS.all[0].open();
  const n = states.length;
  stop();
  expect(FakeWS.all[0].closed).toBe(true);
  FakeWS.all[0].message(JSON.stringify({ api: 1, type: "status", status: {} }));
  FakeWS.all[0].serverClose(1006);
  vi.advanceTimersByTime(60_000);
  expect(FakeWS.all).toHaveLength(1);
  expect(msgs).toEqual([]);
  expect(states).toHaveLength(n);
});

test("stop while a reconnect is pending cancels the timer", () => {
  const { stop } = start();
  FakeWS.all[0].serverClose(1006);
  stop();
  vi.advanceTimersByTime(60_000);
  expect(FakeWS.all).toHaveLength(1);
});

test("delivers telemetry messages and drops malformed ones", () => {
  const { msgs } = start();
  FakeWS.all[0].open();
  const latest = { lat: 1, lon: 2, alt_agl_m: 30, yaw_deg: 90, t_iso: "2026-11-06T06:00:00Z" };
  FakeWS.all[0].message(JSON.stringify({ api: 1, type: "telemetry", latest }));
  FakeWS.all[0].message(JSON.stringify({ api: 1, type: "telemetry", latest: null }));
  FakeWS.all[0].message(JSON.stringify({ api: 1, type: "telemetry" }));
  expect(msgs).toEqual([
    { api: 1, type: "telemetry", latest },
    { api: 1, type: "telemetry", latest: null },
  ]);
});
