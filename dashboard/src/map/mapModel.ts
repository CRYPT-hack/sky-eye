// What the map shows and how it is coloured. No Leaflet in here, so the page, the legend
// and the tests can use it without loading the map library.
import type { DronePosition, Truck } from "../api/types";

export const DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const ATTRIBUTION_TEXT = "© OpenStreetMap contributors";
export const ATTRIBUTION_URL = "https://www.openstreetmap.org/copyright";

/** The engine keeps at most this many track points; the page keeps the same. */
export const MAX_TRACK_POINTS = 600;

export type AgeBucket = "recent" | "today" | "older";

/**
 * Marker colours by age, from the stylesheet palette (--error, --accent, --info) so they
 * read on OpenStreetMap tiles. The legend and the map both use these.
 */
export const AGE_COLORS: Readonly<Record<AgeBucket, string>> = {
  recent: "#b3261e",
  today: "#f2c000",
  older: "#1f5fae",
};

export const AGE_LABELS: Readonly<Record<AgeBucket, string>> = {
  recent: "Last hour",
  today: "Earlier today",
  older: "Older",
};

/** Colour of the drone marker and its track: green, which no age bucket uses. */
export const DRONE_COLOR = "#1e7a4c";

const HOUR_MS = 3_600_000;

/** Last hour, earlier today (local day), or older. A time in the future counts as recent. */
export function ageBucket(iso: string, now: Date): AgeBucket {
  const seen = new Date(iso);
  if (now.getTime() - seen.getTime() < HOUR_MS) return "recent";
  const sameDay =
    seen.getFullYear() === now.getFullYear() &&
    seen.getMonth() === now.getMonth() &&
    seen.getDate() === now.getDate();
  return sameDay ? "today" : "older";
}

/** Text for the attribution control, which Leaflet shows as HTML: never raw config text. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** The tile credit as safe HTML; linked to the OpenStreetMap copyright page only for its tiles. */
export function attributionHtml(text: string, tileUrl: string): string {
  const safe = escapeHtml(text);
  return tileUrl === DEFAULT_TILE_URL
    ? `<a href="${ATTRIBUTION_URL}" target="_blank" rel="noopener noreferrer">${safe}</a>`
    : safe;
}

export type MarkerPlan =
  /** A reliable fix: a filled point, with its error circle when it has a radius. */
  | { kind: "point"; truck: Truck; lat: number; lon: number; radiusM: number | null; color: string }
  /** An unreliable fix: only the circle where it may be. */
  | { kind: "area"; truck: Truck; lat: number; lon: number; radiusM: number; color: string };

/**
 * How one truck is drawn: a filled point (with its error circle) for a reliable fix, an
 * uncertainty circle for an unreliable one. A truck with no usable position is not drawn
 * at all (null).
 */
export function planMarker(truck: Truck, now: Date): MarkerPlan | null {
  const { lat, lon } = truck;
  if (lat === null || lon === null) return null;
  const color = AGE_COLORS[ageBucket(truck.first_seen, now)];
  if (truck.geo_status === "ok") {
    return { kind: "point", truck, lat, lon, radiusM: truck.geo_error_m, color };
  }
  if (truck.geo_status === "unreliable" && truck.geo_error_m !== null) {
    return { kind: "area", truck, lat, lon, radiusM: truck.geo_error_m, color };
  }
  return null;
}

export function planMarkers(trucks: readonly Truck[], now: Date): MarkerPlan[] {
  const plans: MarkerPlan[] = [];
  for (const truck of trucks) {
    const plan = planMarker(truck, now);
    if (plan) plans.push(plan);
  }
  return plans;
}

/** Whether the map can draw this truck. */
export function isLocated(truck: Truck): boolean {
  return planMarker(truck, new Date(0)) !== null;
}

/** The track with the new position added; only the newest `MAX_TRACK_POINTS` stay. */
export function appendTrack(
  track: readonly [number, number][],
  latest: Pick<DronePosition, "lat" | "lon">,
): [number, number][] {
  const next: [number, number][] = [...track, [latest.lat, latest.lon]];
  return next.length > MAX_TRACK_POINTS ? next.slice(next.length - MAX_TRACK_POINTS) : next;
}

/** What a drawn layer was made from: `shape` is geometry and popup inputs, `color` the age colour. */
export interface LayerState {
  shape: string;
  color: string;
}

function shapeKey(plan: MarkerPlan): string {
  const t = plan.truck;
  const radius = plan.radiusM ?? "";
  return [plan.kind, plan.lat, plan.lon, radius, t.crop_url, t.first_seen, t.best_score].join("|");
}

export type LayerChange =
  | { action: "add" | "replace" | "recolor"; plan: MarkerPlan }
  | { action: "remove"; id: number };

/**
 * What to do to the layers already on the map so they match `plans`. A layer whose shape
 * and colour are unchanged is not listed, so it stays as it is (and keeps an open popup);
 * a colour-only change recolours in place; a moved or edited truck is replaced.
 */
export function reconcileMarkers(
  drawn: ReadonlyMap<number, LayerState>,
  plans: readonly MarkerPlan[],
): LayerChange[] {
  const changes: LayerChange[] = [];
  const wanted = new Set<number>();
  for (const plan of plans) {
    const id = plan.truck.id;
    wanted.add(id);
    const have = drawn.get(id);
    if (!have) changes.push({ action: "add", plan });
    else if (have.shape !== shapeKey(plan)) changes.push({ action: "replace", plan });
    else if (have.color !== plan.color) changes.push({ action: "recolor", plan });
  }
  for (const id of drawn.keys()) if (!wanted.has(id)) changes.push({ action: "remove", id });
  return changes;
}

/** The state to remember for a layer just drawn from `plan`. */
export function layerState(plan: MarkerPlan): LayerState {
  return { shape: shapeKey(plan), color: plan.color };
}
