import type { Info, Runtime, Status, Telemetry, Truck } from "./types";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface TruckQuery {
  from?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

export interface ApiClient {
  getInfo(): Promise<Info>;
  getStatus(): Promise<Status>;
  listTrucks(q?: TruckQuery): Promise<{ trucks: Truck[]; limit: number; offset: number }>;
  getTruck(id: number): Promise<Truck>;
  /** The drone's latest position and its track over the last `seconds` (1-3600). */
  getTelemetry(seconds?: number): Promise<Telemetry>;
  getRuntime(): Promise<Runtime>;
  putRuntime(patch: Partial<Runtime>): Promise<Runtime>;
  /** Sends a test alert; resolves with the channels it reached (may be empty). */
  sendTestAlert(): Promise<string[]>;
  mediaUrl(path: string): string;
  streamUrl(): string;
  liveUrl(): string;
}

export interface ClientOptions {
  getToken: () => string | null;
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  location?: { protocol: string; host: string };
}

export function createClient(opts: ClientOptions): ApiClient {
  const baseUrl = opts.baseUrl ?? "";

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = new Headers(init.headers);
    const token = opts.getToken();
    if (token) headers.set("X-SkyEye-Token", token);
    const doFetch = opts.fetchImpl ?? ((input, i) => fetch(input, i));
    const res = await doFetch(baseUrl + path, { ...init, headers });
    if (!res.ok) {
      let message = res.statusText;
      try {
        const body = (await res.json()) as { error?: unknown };
        if (typeof body?.error === "string") message = body.error;
      } catch {
        // non-JSON error body: keep the status text
      }
      throw new ApiError(res.status, message);
    }
    return (await res.json()) as T;
  }

  function withToken(path: string): string {
    const token = opts.getToken();
    if (!token) return baseUrl + path;
    const sep = path.includes("?") ? "&" : "?";
    return `${baseUrl}${path}${sep}token=${encodeURIComponent(token)}`;
  }

  return {
    async getInfo() {
      return (await request<{ info: Info }>("/api/info")).info;
    },
    async getStatus() {
      return (await request<{ status: Status }>("/api/status")).status;
    },
    async listTrucks(q = {}) {
      const params = new URLSearchParams();
      for (const key of ["from", "to", "limit", "offset"] as const) {
        const v = q[key];
        if (v !== undefined) params.set(key, String(v));
      }
      const qs = params.toString();
      const r = await request<{ trucks: Truck[]; limit: number; offset: number }>(
        `/api/trucks${qs ? `?${qs}` : ""}`,
      );
      return { trucks: r.trucks, limit: r.limit, offset: r.offset };
    },
    async getTruck(id) {
      return (await request<{ truck: Truck }>(`/api/trucks/${id}`)).truck;
    },
    async getTelemetry(seconds = 300) {
      const r = await request<Telemetry>(`/api/telemetry?seconds=${seconds}`);
      return { state: r.state, latest: r.latest, track: r.track };
    },
    async getRuntime() {
      return (await request<{ runtime: Runtime }>("/api/config/runtime")).runtime;
    },
    async putRuntime(patch) {
      const r = await request<{ runtime: Runtime }>("/api/config/runtime", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      return r.runtime;
    },
    async sendTestAlert() {
      const r = await request<{ sent: string[] }>("/api/alerts/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      return r.sent;
    },
    mediaUrl: withToken,
    streamUrl() {
      return withToken("/stream.mjpg");
    },
    liveUrl() {
      const loc = opts.location ?? window.location;
      const scheme = loc.protocol === "https:" ? "wss:" : "ws:";
      const token = opts.getToken();
      const q = token ? `?token=${encodeURIComponent(token)}` : "";
      return `${scheme}//${loc.host}/ws${q}`;
    },
  };
}
