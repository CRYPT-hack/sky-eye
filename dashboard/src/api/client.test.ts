import { expect, test, vi } from "vitest";
import { ApiError, createClient } from "./client";

const STATUS = {
  source_state: "running",
  fps: 30,
  frames_processed: 10,
  trucks_now: 1,
  trucks_total: 2,
  started_at: "2026-11-06T04:30:00Z",
  telemetry_state: "none",
};

function json(body: unknown, status = 200, statusText = "OK") {
  return new Response(JSON.stringify(body), { status, statusText });
}

function mk(token: string | null, fetchImpl: unknown, extra = {}) {
  return createClient({ getToken: () => token, fetchImpl: fetchImpl as typeof fetch, ...extra });
}

type Call = [string, RequestInit | undefined];

test("sends token header and unwraps status", async () => {
  const f = vi.fn(async () => json({ api: 1, status: STATUS }));
  const status = await mk("abc", f).getStatus();
  expect(status).toEqual(STATUS);
  const [url, init] = f.mock.calls[0] as unknown as Call;
  expect(url).toBe("/api/status");
  expect(new Headers(init?.headers).get("X-SkyEye-Token")).toBe("abc");
});

test("omits token header when no token", async () => {
  const f = vi.fn(async () => json({ api: 1, status: STATUS }));
  await mk(null, f).getStatus();
  const [, init] = f.mock.calls[0] as unknown as Call;
  expect(new Headers(init?.headers).has("X-SkyEye-Token")).toBe(false);
});

test("401 raises ApiError", async () => {
  const f = vi.fn(async () => json({ api: 1, error: "unauthorized" }, 401, "Unauthorized"));
  const p = mk(null, f).getInfo();
  await expect(p).rejects.toBeInstanceOf(ApiError);
  await expect(p).rejects.toMatchObject({ status: 401, message: "unauthorized" });
});

test("non-JSON error body falls back to status text", async () => {
  const f = vi.fn(async () => new Response("<html>", { status: 502, statusText: "Bad Gateway" }));
  await expect(mk(null, f).getStatus()).rejects.toMatchObject({
    status: 502,
    message: "Bad Gateway",
  });
});

test("listTrucks builds query", async () => {
  const f = vi.fn(async () => json({ api: 1, trucks: [], limit: 20, offset: 0 }));
  const r = await mk(null, f).listTrucks({ from: "2026-11-06T00:00:00.000Z", limit: 20 });
  expect(r).toEqual({ trucks: [], limit: 20, offset: 0 });
  expect((f.mock.calls[0] as unknown as Call)[0]).toBe(
    "/api/trucks?from=2026-11-06T00%3A00%3A00.000Z&limit=20",
  );
});

test("listTrucks without query and getTruck unwrap", async () => {
  const truck = { id: 3 };
  const f = vi.fn(async () => json({ api: 1, truck, trucks: [], limit: 1, offset: 0 }));
  const c = mk(null, f);
  await c.listTrucks();
  expect((f.mock.calls[0] as unknown as Call)[0]).toBe("/api/trucks");
  expect(await c.getTruck(3)).toEqual(truck);
  expect((f.mock.calls[1] as unknown as Call)[0]).toBe("/api/trucks/3");
});

test("putRuntime sends JSON PUT and unwraps runtime", async () => {
  const f = vi.fn(async () => json({ api: 1, runtime: { confidence: 0.4, sahi_enabled: false } }));
  const r = await mk("t", f, { baseUrl: "http://h:8000" }).putRuntime({ confidence: 0.4 });
  expect(r).toEqual({ confidence: 0.4, sahi_enabled: false });
  const [url, init] = f.mock.calls[0] as unknown as Call;
  expect(url).toBe("http://h:8000/api/config/runtime");
  expect(init?.method).toBe("PUT");
  const h = new Headers(init?.headers);
  expect(h.get("Content-Type")).toBe("application/json");
  expect(h.get("X-SkyEye-Token")).toBe("t");
  expect(init?.body).toBe(JSON.stringify({ confidence: 0.4 }));
});

test("mediaUrl keeps version and adds token", () => {
  const f = vi.fn();
  expect(mk("a b", f).mediaUrl("/media/x.jpg?v=9000")).toBe("/media/x.jpg?v=9000&token=a%20b");
  expect(mk("a b", f).mediaUrl("/media/x%20y.mp4")).toBe("/media/x%20y.mp4?token=a%20b");
  expect(mk(null, f).mediaUrl("/media/x.jpg?v=9000")).toBe("/media/x.jpg?v=9000");
  expect(mk("t", f, { baseUrl: "http://h" }).mediaUrl("/media/x.jpg")).toBe(
    "http://h/media/x.jpg?token=t",
  );
  expect(mk("t", f).streamUrl()).toBe("/stream.mjpg?token=t");
});

test("liveUrl uses wss on https", () => {
  const f = vi.fn();
  const https = { protocol: "https:", host: "ex.com:8443" };
  expect(mk("a b", f, { location: https }).liveUrl()).toBe("wss://ex.com:8443/ws?token=a%20b");
  expect(mk(null, f, { location: { protocol: "http:", host: "h:1" } }).liveUrl()).toBe(
    "ws://h:1/ws",
  );
});

test("sendTestAlert POSTs JSON {} and returns the channels", async () => {
  const f = vi.fn(async () => json({ api: 1, sent: ["desktop", "telegram"] }));
  const sent = await mk("t", f).sendTestAlert();
  expect(sent).toEqual(["desktop", "telegram"]);
  const [url, init] = f.mock.calls[0] as unknown as Call;
  expect(url).toBe("/api/alerts/test");
  expect(init?.method).toBe("POST");
  const h = new Headers(init?.headers);
  expect(h.get("Content-Type")).toBe("application/json");
  expect(h.get("X-SkyEye-Token")).toBe("t");
  expect(init?.body).toBe("{}");
});

test("sendTestAlert surfaces the API error", async () => {
  const f = vi.fn(async () =>
    json({ api: 1, error: "no alert channels configured" }, 409, "Conflict"),
  );
  await expect(mk("t", f).sendTestAlert()).rejects.toMatchObject({
    status: 409,
    message: "no alert channels configured",
  });
});

test("getTelemetry asks for the seconds and returns the body without api", async () => {
  const body = {
    api: 1,
    state: "ok",
    latest: { lat: 1, lon: 2, alt_agl_m: 30, yaw_deg: 90, t_iso: "2026-11-06T06:00:00Z" },
    track: [[1, 2]],
  };
  const f = vi.fn(async () => json(body));
  const r = await mk("abc", f).getTelemetry(300);
  expect(r).toEqual({ state: "ok", latest: body.latest, track: [[1, 2]] });
  const [url, init] = f.mock.calls[0] as unknown as Call;
  expect(url).toBe("/api/telemetry?seconds=300");
  expect(new Headers(init?.headers).get("X-SkyEye-Token")).toBe("abc");
});
