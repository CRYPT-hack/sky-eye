import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";
import { href, parseHash, useRoute, type Route } from "./router";

afterEach(() => {
  window.location.hash = "";
});

test("parseHash maps routes", () => {
  expect(parseHash("")).toEqual({ page: "live" });
  expect(parseHash("#/trucks")).toEqual({ page: "trucks" });
  expect(parseHash("#/trucks/12")).toEqual({ page: "truck", id: 12 });
  expect(parseHash("#/trucks/abc")).toEqual({ page: "trucks" });
  expect(parseHash("#/status")).toEqual({ page: "status" });
  expect(parseHash("#/nope")).toEqual({ page: "live" });
});

describe("parseHash edge cases", () => {
  test("bare hash and live", () => {
    expect(parseHash("#")).toEqual({ page: "live" });
    expect(parseHash("#/")).toEqual({ page: "live" });
    expect(parseHash("#/live")).toEqual({ page: "live" });
  });

  test("trailing slash is ignored", () => {
    expect(parseHash("#/trucks/")).toEqual({ page: "trucks" });
    expect(parseHash("#/trucks/7/")).toEqual({ page: "truck", id: 7 });
  });

  test("only whole positive integers are truck ids", () => {
    expect(parseHash("#/trucks/1.5")).toEqual({ page: "trucks" });
    expect(parseHash("#/trucks/-3")).toEqual({ page: "trucks" });
    expect(parseHash("#/trucks/1e3")).toEqual({ page: "trucks" });
    expect(parseHash("#/trucks/12abc")).toEqual({ page: "trucks" });
    expect(parseHash("#/trucks/99999999999999999999")).toEqual({ page: "trucks" });
  });
});

test("href round-trips through parseHash", () => {
  const routes: Route[] = [
    { page: "live" },
    { page: "trucks" },
    { page: "truck", id: 42 },
    { page: "status" },
  ];
  expect(routes.map(href)).toEqual(["#/live", "#/trucks", "#/trucks/42", "#/status"]);
  for (const route of routes) {
    expect(parseHash(href(route))).toEqual(route);
  }
});

test("useRoute follows hashchange", () => {
  window.location.hash = "#/trucks/5";
  const { result } = renderHook(() => useRoute());
  expect(result.current).toEqual({ page: "truck", id: 5 });

  act(() => {
    window.location.hash = "#/status";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  expect(result.current).toEqual({ page: "status" });
});
