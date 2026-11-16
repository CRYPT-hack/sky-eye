import { expect, test } from "vitest";
import {
  formatDuration,
  formatScore,
  formatSourceState,
  formatTelemetryState,
  formatTime,
} from "./format";

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
  expect(formatDuration(t0, "2026-11-06T05:29:59.000Z")).toBe("59 min 59 s");
});

test("formatDuration shows hours from 1 h, without seconds", () => {
  const t0 = "2026-11-06T04:30:00.000Z";
  expect(formatDuration(t0, "2026-11-06T05:30:00.000Z")).toBe("1 h 0 min");
  expect(formatDuration(t0, "2026-11-06T06:35:59.000Z")).toBe("2 h 5 min");
  expect(formatDuration(t0, "2026-11-08T04:30:00.000Z")).toBe("48 h 0 min");
});

test("formatSourceState labels known states and passes unknown ones through", () => {
  expect(formatSourceState("starting")).toBe("Starting");
  expect(formatSourceState("running")).toBe("Running");
  expect(formatSourceState("ended")).toBe("Ended");
  expect(formatSourceState("error")).toBe("Error");
  expect(formatSourceState("paused")).toBe("paused");
  expect(formatSourceState("toString")).toBe("toString");
});

test("formatScore", () => {
  expect(formatScore(0.87)).toBe("87%");
});

test("connecting source state has a label", () => {
  expect(formatSourceState("connecting")).toBe("Connecting");
});

test("reconnecting source state has a label", async () => {
  const { formatSourceState } = await import("./format");
  expect(formatSourceState("reconnecting")).toBe("Reconnecting");
});

test("formatTelemetryState labels the drone position states and passes unknown ones through", () => {
  expect(formatTelemetryState("none")).toBe("Off");
  expect(formatTelemetryState("waiting")).toBe("Waiting");
  expect(formatTelemetryState("ok")).toBe("OK");
  expect(formatTelemetryState("stale")).toBe("Stale");
  expect(formatTelemetryState("off")).toBe("off");
  expect(formatTelemetryState("toString")).toBe("toString");
});
