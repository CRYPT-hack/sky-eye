import type { LiveMessage } from "./types";

export type LiveState = "connecting" | "open" | "reconnecting" | "unauthorized";

export interface LiveOptions {
  /** Called on every (re)connect, so a changed token is picked up. */
  url: () => string;
  onMessage: (m: LiveMessage) => void;
  onState: (s: LiveState) => void;
  WebSocketImpl?: typeof WebSocket;
  backoffMs?: number[];
}

const DEFAULT_BACKOFF = [1000, 2000, 5000, 10000];
const UNAUTHORIZED = 4401;

function parseMessage(data: unknown): LiveMessage | null {
  if (typeof data !== "string") return null;
  let m: unknown;
  try {
    m = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof m !== "object" || m === null) return null;
  const o = m as Record<string, unknown>;
  const isObj = (v: unknown) => typeof v === "object" && v !== null;
  if (o.type === "status" && isObj(o.status)) return m as LiveMessage;
  if ((o.type === "truck.new" || o.type === "truck.updated") && isObj(o.truck)) {
    return m as LiveMessage;
  }
  return null;
}

/** Keeps one WebSocket alive with backoff. Returns a function that stops everything. */
export function connectLive(opts: LiveOptions): () => void {
  const Impl = opts.WebSocketImpl ?? WebSocket;
  const backoff = opts.backoffMs && opts.backoffMs.length > 0 ? opts.backoffMs : DEFAULT_BACKOFF;
  let stopped = false;
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;

  const setState = (s: LiveState) => {
    if (!stopped) opts.onState(s);
  };

  function scheduleReconnect() {
    setState("reconnecting");
    const delay = backoff[Math.min(attempt, backoff.length - 1)];
    attempt += 1;
    timer = setTimeout(() => {
      timer = null;
      open();
    }, delay);
  }

  function open() {
    if (stopped) return;
    setState(attempt === 0 ? "connecting" : "reconnecting");
    let ws: WebSocket;
    try {
      ws = new Impl(opts.url());
    } catch {
      scheduleReconnect();
      return;
    }
    socket = ws;
    ws.onopen = () => {
      if (stopped || socket !== ws) return;
      attempt = 0;
      setState("open");
    };
    ws.onmessage = (e) => {
      if (stopped || socket !== ws) return;
      const msg = parseMessage(e.data);
      if (msg) opts.onMessage(msg);
    };
    ws.onclose = (e) => {
      if (stopped || socket !== ws) return;
      socket = null;
      if (e.code === UNAUTHORIZED) setState("unauthorized");
      else scheduleReconnect();
    };
  }

  open();

  return () => {
    stopped = true;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    const ws = socket;
    socket = null;
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
      try {
        ws.close();
      } catch {
        // already closed
      }
    }
  };
}
