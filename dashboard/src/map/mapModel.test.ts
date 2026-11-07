import { describe, expect, test } from "vitest";
import { makeTruck } from "../live/fakes";
import {
  AGE_COLORS,
  MAX_TRACK_POINTS,
  ageBucket,
  appendTrack,
  isLocated,
  planMarkers,
} from "./mapModel";

// 11:33 on 6 Nov 2026 in Asia/Kolkata (the zone the tests run in).
const NOW = new Date("2026-11-06T06:03:00Z");

describe("ageBucket", () => {
  test("last hour, earlier today (local day), older", () => {
    expect(ageBucket("2026-11-06T05:30:00Z", NOW)).toBe("recent");
    expect(ageBucket("2026-11-06T05:03:01Z", NOW)).toBe("recent");
    // 90 minutes ago, still today in India
    expect(ageBucket("2026-11-06T04:33:00Z", NOW)).toBe("today");
    // 00:30 local on 6 Nov
    expect(ageBucket("2026-11-05T19:00:00Z", NOW)).toBe("today");
    // 23:30 local on 5 Nov
    expect(ageBucket("2026-11-05T18:00:00Z", NOW)).toBe("older");
    expect(ageBucket("2026-10-01T00:00:00Z", NOW)).toBe("older");
  });

  test("a time in the future counts as recent", () => {
    expect(ageBucket("2026-11-06T06:10:00Z", NOW)).toBe("recent");
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
    first_seen: "2026-11-06T05:50:00Z",
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
