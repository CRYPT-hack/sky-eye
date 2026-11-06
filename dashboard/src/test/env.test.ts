import { expect, test } from "vitest";

test("tests run in the Asia/Kolkata time zone", () => {
  // 04:30 UTC is 10:00 in India (UTC+05:30, no daylight saving).
  const date = new Date("2026-11-06T04:30:00Z");
  expect(date.getHours()).toBe(10);
  expect(date.getMinutes()).toBe(0);
  expect(date.getTimezoneOffset()).toBe(-330);
});
