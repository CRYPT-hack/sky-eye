import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, test, vi } from "vitest";
import { ApiProvider, useApi } from "../apiContext";
import { ApiError } from "../api/client";
import { makeClient, makeConnect, makeTruck, STATUS } from "./fakes";
import { useLive } from "./useLive";

function wrap(client: ReturnType<typeof makeClient>) {
  return ({ children }: { children: ReactNode }) => (
    <ApiProvider client={client}>{children}</ApiProvider>
  );
}

test("dedupes trucks by id and applies updates", async () => {
  const client = makeClient([makeTruck(1)]);
  const fake = makeConnect();
  const { result } = renderHook(() => useLive(client, { connect: fake.connect }), {
    wrapper: wrap(client),
  });
  await waitFor(() => expect(result.current.trucks).toHaveLength(1));

  act(() => fake.send({ type: "truck.new", truck: makeTruck(1) }));
  act(() => fake.send({ type: "truck.updated", truck: makeTruck(1, { best_score: 0.95 }) }));

  expect(result.current.trucks).toHaveLength(1);
  expect(result.current.trucks[0].best_score).toBe(0.95);
});

test("truck.updated for an unknown id adds it", async () => {
  const client = makeClient([]);
  const fake = makeConnect();
  const { result } = renderHook(() => useLive(client, { connect: fake.connect }), {
    wrapper: wrap(client),
  });
  await waitFor(() => expect(client.listTrucks).toHaveBeenCalled());
  act(() => fake.send({ type: "truck.updated", truck: makeTruck(4) }));
  expect(result.current.trucks.map((t) => t.id)).toEqual([4]);
});

test("new trucks go first and the list is capped", async () => {
  const client = makeClient([]);
  const fake = makeConnect();
  const { result } = renderHook(() => useLive(client, { connect: fake.connect, keep: 3 }), {
    wrapper: wrap(client),
  });
  await waitFor(() => expect(client.listTrucks).toHaveBeenCalled());
  for (let i = 1; i <= 5; i++) act(() => fake.send({ type: "truck.new", truck: makeTruck(i) }));
  expect(result.current.trucks.map((t) => t.id)).toEqual([5, 4, 3]);
});

test("equal first_seen: higher id first", async () => {
  const same = "2026-11-06T06:00:00.000Z";
  const client = makeClient([makeTruck(1, { first_seen: same }), makeTruck(2, { first_seen: same })]);
  const fake = makeConnect();
  const { result } = renderHook(() => useLive(client, { connect: fake.connect }), {
    wrapper: wrap(client),
  });
  await waitFor(() => expect(result.current.trucks).toHaveLength(2));
  expect(result.current.trucks.map((t) => t.id)).toEqual([2, 1]);
});

test("status messages replace the status and state is passed on", async () => {
  const client = makeClient([]);
  const fake = makeConnect();
  const { result } = renderHook(() => useLive(client, { connect: fake.connect }), {
    wrapper: wrap(client),
  });
  await waitFor(() => expect(result.current.status).toEqual(STATUS));
  act(() => fake.send({ type: "status", status: { ...STATUS, trucks_now: 2 } }));
  expect(result.current.status?.trucks_now).toBe(2);
  act(() => fake.state("open"));
  expect(result.current.state).toBe("open");
});

test("unauthorized live state reports unauthorized", async () => {
  const client = makeClient([]);
  const fake = makeConnect();
  const { result } = renderHook(
    () => ({ live: useLive(client, { connect: fake.connect }), api: useApi() }),
    { wrapper: wrap(client) },
  );
  expect(result.current.api.unauthorized).toBe(false);
  act(() => fake.state("unauthorized"));
  expect(result.current.api.unauthorized).toBe(true);
});

test("a 401 on the initial fetch reports unauthorized", async () => {
  const client = makeClient([]);
  (client.listTrucks as ReturnType<typeof vi.fn>).mockRejectedValue(new ApiError(401, "no"));
  const fake = makeConnect();
  const { result } = renderHook(
    () => ({ live: useLive(client, { connect: fake.connect }), api: useApi() }),
    { wrapper: wrap(client) },
  );
  await waitFor(() => expect(result.current.api.unauthorized).toBe(true));
});

test("changing the token refetches and reconnects", async () => {
  const client = makeClient([]);
  const fake = makeConnect();
  const { result } = renderHook(
    () => ({ live: useLive(client, { connect: fake.connect }), api: useApi() }),
    { wrapper: wrap(client) },
  );
  await waitFor(() => expect(client.listTrucks).toHaveBeenCalledTimes(1));
  expect(fake.calls).toHaveLength(1);

  act(() => result.current.api.setToken("secret"));

  await waitFor(() => expect(client.listTrucks).toHaveBeenCalledTimes(2));
  expect(fake.calls).toHaveLength(2);
  expect(fake.stops[0]).toHaveBeenCalled();
});

test("stops the connection on unmount", async () => {
  const client = makeClient([]);
  const fake = makeConnect();
  const { unmount } = renderHook(() => useLive(client, { connect: fake.connect }), {
    wrapper: wrap(client),
  });
  unmount();
  expect(fake.stops[0]).toHaveBeenCalled();
});
