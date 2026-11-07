import { describe, expect, test } from "vitest";
import { makeTruck } from "../live/fakes";
import {
  AGE_COLORS,
  MAX_TRACK_POINTS,
  ageBucket,
  layerState,
  reconcileMarkers,
  type LayerState,
  appendTrack,
  isLocated,
  planMarkers,
} from "./mapModel";

// Local clock times (Date constructors), so the local-day buckets hold in any time zone.
const at = (day: number, h: number, min: number, s = 0) =>
  new Date(2026, 10, day, h, min, s).toISOString();
const NOW = new Date(2026, 10, 6, 11, 33, 0);

describe("ageBucket", () => {
  test("last hour, earlier today (local day), older", () => {
    expect(ageBucket(at(6, 11, 0), NOW)).toBe("recent");
    expect(ageBucket(at(6, 10, 33, 1), NOW)).toBe("recent");
    // 90 minutes ago, still today
    expect(ageBucket(at(6, 10, 3), NOW)).toBe("today");
    expect(ageBucket(at(6, 0, 30), NOW)).toBe("today");
    // 23:30 the day before
    expect(ageBucket(at(5, 23, 30), NOW)).toBe("older");
    expect(ageBucket("2026-10-01T00:00:00Z", NOW)).toBe("older");
  });

  test("a time in the future counts as recent", () => {
    expect(ageBucket(at(6, 11, 40), NOW)).toBe("recent");
  });

  test("three different colours", () => {
    expect(new Set(Object.values(AGE_COLORS)).size).toBe(3);
  });
});

describe("planMarkers", () => {
  const ok = makeTruck(1, {
    lat: 12.5,
    lon: 77.5,
    geo_error_m: 4.2,
    geo_status: "ok",
    first_seen: at(6, 11, 20),
  });
  const rough = makeTruck(2, {
    lat: 12.6,
    lon: 77.6,
    geo_error_m: 180,
    geo_status: "unreliable",
    first_seen: "2026-10-01T00:00:00Z",
  });

  test("renders ok and unreliable trucks differently", () => {
    const plan = planMarkers([ok, rough], NOW);
    expect(plan).toEqual([
      { kind: "point", truck: ok, lat: 12.5, lon: 77.5, color: AGE_COLORS.recent },
      { kind: "area", truck: rough, lat: 12.6, lon: 77.6, radiusM: 180, color: AGE_COLORS.older },
    ]);
  });

  test("trucks without a usable position are not drawn", () => {
    const noPos = makeTruck(3, { geo_status: "unreliable" });
    const unknown = makeTruck(4, { geo_status: "unknown", lat: 1, lon: 2 });
    const none = makeTruck(5);
    const okNoLat = makeTruck(6, { geo_status: "ok", lat: null, lon: 2 });
    const roughNoRadius = makeTruck(7, { geo_status: "unreliable", lat: 1, lon: 2, geo_error_m: null });
    const all = [noPos, unknown, none, okNoLat, roughNoRadius];
    expect(planMarkers(all, NOW)).toEqual([]);
    expect(all.some(isLocated)).toBe(false);
    expect(isLocated(ok)).toBe(true);
    expect(isLocated(rough)).toBe(true);
  });
});

describe("appendTrack", () => {
  test("appends the new position and keeps the newest 600 points", () => {
    let track: [number, number][] = [];
    for (let i = 0; i < MAX_TRACK_POINTS + 50; i++) track = appendTrack(track, { lat: i, lon: 0 });
    expect(track).toHaveLength(MAX_TRACK_POINTS);
    expect(track[0]).toEqual([50, 0]);
    expect(track.at(-1)).toEqual([MAX_TRACK_POINTS + 49, 0]);
  });

  test("does not change the list it was given", () => {
    const before: [number, number][] = [[1, 1]];
    const after = appendTrack(before, { lat: 2, lon: 2 });
    expect(before).toEqual([[1, 1]]);
    expect(after).toEqual([
      [1, 1],
      [2, 2],
    ]);
  });
});

describe("reconcileMarkers", () => {
  const a = makeTruck(1, {
    lat: 1,
    lon: 2,
    geo_error_m: 3,
    geo_status: "ok",
    first_seen: at(6, 11, 20),
  });
  const b = makeTruck(2, {
    lat: 3,
    lon: 4,
    geo_error_m: 90,
    geo_status: "unreliable",
    first_seen: at(6, 11, 20),
  });

  function drawnFrom(trucks: (typeof a)[], now: Date) {
    return new Map<number, LayerState>(
      planMarkers(trucks, now).map((p) => [p.truck.id, layerState(p)]),
    );
  }

  test("unchanged trucks are left alone, so their popups stay open", () => {
    const drawn = drawnFrom([a, b], NOW);
    expect(reconcileMarkers(drawn, planMarkers([a, b], NOW))).toEqual([]);
    // an update message with the same data (a new object) changes nothing either
    expect(reconcileMarkers(drawn, planMarkers([{ ...a }, { ...b }], NOW))).toEqual([]);
  });

  test("only what changed is touched", () => {
    const drawn = drawnFrom([a, b], NOW);
    const c = makeTruck(3, {
      lat: 5,
      lon: 6,
      geo_error_m: 3,
      geo_status: "ok",
      first_seen: at(6, 11, 25),
    });
    const moved = { ...a, lat: 1.5 };
    expect(reconcileMarkers(drawn, planMarkers([moved, c], NOW))).toEqual([
      { action: "replace", plan: expect.objectContaining({ truck: moved }) },
      { action: "add", plan: expect.objectContaining({ truck: c }) },
      { action: "remove", id: 2 },
    ]);
  });

  test("a new age bucket only recolours", () => {
    const drawn = drawnFrom([a], NOW);
    const later = new Date(2026, 10, 6, 13, 0, 0);
    expect(reconcileMarkers(drawn, planMarkers([a], later))).toEqual([
      { action: "recolor", plan: expect.objectContaining({ color: AGE_COLORS.today }) },
    ]);
  });
});
