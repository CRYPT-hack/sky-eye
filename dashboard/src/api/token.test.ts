import { afterEach, expect, test, vi } from "vitest";
import { loadToken, saveToken } from "./token";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

test("saves, loads and removes the token", () => {
  expect(loadToken()).toBeNull();
  saveToken("abc");
  expect(localStorage.getItem("skyeye.token")).toBe("abc");
  expect(loadToken()).toBe("abc");
  saveToken(null);
  expect(loadToken()).toBeNull();
});

test("token storage survives throwing localStorage", () => {
  const boom = () => {
    throw new Error("blocked");
  };
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(boom);
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(boom);
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(boom);
  expect(loadToken()).toBeNull();
  expect(() => saveToken("x")).not.toThrow();
  expect(() => saveToken(null)).not.toThrow();
});
