import { expect, test } from "vitest";
import { localDayRange } from "./dateRange";

test("localDayRange is end-exclusive local days", () => {
  expect(localDayRange("2026-11-06", "2026-11-06")).toEqual({
    from: "2026-11-05T18:30:00.000Z",
    to: "2026-11-06T18:30:00.000Z",
  });
});

test("localDayRange omits empty ends", () => {
  expect(localDayRange("", "")).toEqual({});
  expect(localDayRange("2026-11-06", "")).toEqual({ from: "2026-11-05T18:30:00.000Z" });
  expect(localDayRange("", "2026-11-30")).toEqual({ to: "2026-11-30T18:30:00.000Z" });
});
