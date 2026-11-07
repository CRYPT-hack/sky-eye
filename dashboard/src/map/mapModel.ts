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

export type MarkerPlan =
  | { kind: "point"; truck: Truck; lat: number; lon: number; color: string }
  | { kind: "area"; truck: Truck; lat: number; lon: number; radiusM: number; color: string };

/**
 * How one truck is drawn: a filled point for a reliable fix, an uncertainty circle for an
 * unreliable one. A truck with no usable position is not drawn at all (null).
 */
export function planMarker(truck: Truck, now: Date): MarkerPlan | null {
  const { lat, lon } = truck;
  if (lat === null || lon === null) return null;
  const color = AGE_COLORS[ageBucket(truck.first_seen, now)];
  if (truck.geo_status === "ok") return { kind: "point", truck, lat, lon, color };
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
