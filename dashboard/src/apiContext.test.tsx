import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, expect, test } from "vitest";
import { ApiProvider, useApi } from "./apiContext";
import { makeClient } from "./live/fakes";

beforeEach(() => window.localStorage.clear());

function wrapper({ children }: { children: ReactNode }) {
  return (
    <ApiProvider client={makeClient()} initialToken="t">
      {children}
    </ApiProvider>
  );
}

test("every setToken call starts a new auth epoch, even with the same token", () => {
  const { result } = renderHook(() => useApi(), { wrapper });
  expect(result.current.authEpoch).toBe(0);

  act(() => result.current.reportUnauthorized());
  expect(result.current.unauthorized).toBe(true);
  act(() => result.current.setToken("t")); // the same token saved again
  expect(result.current.authEpoch).toBe(1);
  expect(result.current.unauthorized).toBe(false);
  expect(result.current.token).toBe("t");

  act(() => result.current.setToken("t"));
  expect(result.current.authEpoch).toBe(2);
  act(() => result.current.setToken("other"));
  expect(result.current.authEpoch).toBe(3);
  expect(window.localStorage.getItem("skyeye.token")).toBe("other");
});
