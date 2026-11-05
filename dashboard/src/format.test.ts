import { expect, test } from "vitest";
import { formatDuration, formatScore, formatTime } from "./format";

test("formatTime uses local zone", () => {
  const now = new Date("2026-11-06T08:00:00Z");
  expect(formatTime("2026-11-06T04:30:00Z", now)).toBe("10:00:00");
});

test("formatTime shows date for other days", () => {
  const now = new Date("2026-11-06T08:00:00Z");
  expect(formatTime("2026-11-04T04:30:00Z", now)).toBe("4 Nov 10:00");
});

test("formatDuration", () => {
  const t0 = "2026-11-06T04:30:00.000Z";
  expect(formatDuration(t0, "2026-11-06T04:30:00.400Z")).toBe("<1 s");
  expect(formatDuration(t0, "2026-11-06T04:30:12.000Z")).toBe("12 s");
  expect(formatDuration(t0, "2026-11-06T04:33:04.000Z")).toBe("3 min 4 s");
});

test("formatScore", () => {
  expect(formatScore(0.87)).toBe("87%");
});
