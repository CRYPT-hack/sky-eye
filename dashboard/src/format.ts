import type { SourceState } from "./api/types";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const pad = (n: number) => String(n).padStart(2, "0");

/** Local `HH:MM:SS` when on the same local day as `now`, otherwise `D MMM HH:MM`. */
export function formatTime(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** `<1 s`, `12 s`, `3 min 4 s`; from an hour on `2 h 5 min` (no seconds). */
export function formatDuration(fromIso: string, toIso: string): string {
  const ms = new Date(toIso).getTime() - new Date(fromIso).getTime();
  const total = Math.floor(Math.max(0, ms) / 1000);
  if (total < 1) return "<1 s";
  if (total < 60) return `${total} s`;
  if (total < 3600) return `${Math.floor(total / 60)} min ${total % 60} s`;
  return `${Math.floor(total / 3600)} h ${Math.floor((total % 3600) / 60)} min`;
}

/** Labels for the engine's source states, shared by the live and status pages. */
export const SOURCE_LABEL: Readonly<Record<SourceState, string>> = {
  starting: "Starting",
  connecting: "Connecting",
  running: "Running",
  reconnecting: "Reconnecting",
  ended: "Ended",
  error: "Error",
};

/** The label for a source state; a state this site does not know yet is shown as sent. */
export function formatSourceState(state: string): string {
  return Object.hasOwn(SOURCE_LABEL, state) ? SOURCE_LABEL[state as SourceState] : state;
}

/** Processed frames per second, one decimal: `24.6`. */
export function formatFps(fps: number): string {
  return fps.toFixed(1);
}

/** `0.87` -> `87%`. */
export function formatScore(score: number): string {
  return `${Math.round(score * 100)}%`;
}

const TELEMETRY_LABEL: Readonly<Record<string, string>> = {
  none: "Off",
  waiting: "Waiting",
  ok: "OK",
  stale: "Stale",
};

/** Drone position state as shown on the status page; a state this site does not know is shown as sent. */
export function formatTelemetryState(state: string): string {
  return Object.hasOwn(TELEMETRY_LABEL, state) ? TELEMETRY_LABEL[state] : state;
}

const ALERT_LABEL: Readonly<Record<string, string>> = {
  desktop: "Laptop",
  telegram: "Telegram",
  mqtt: "MQTT",
};

/** Display name of an alert channel; a channel this site does not know is shown as sent. */
export function formatAlertChannel(name: string): string {
  return Object.hasOwn(ALERT_LABEL, name) ? ALERT_LABEL[name] : name;
}
