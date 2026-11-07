import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ApiError } from "../api/client";
import type { DronePosition, Info, Status, Truck } from "../api/types";
import { ApiProvider, useApi } from "../apiContext";
import TokenPrompt from "../components/TokenPrompt";
import { makeClient, makeConnect, makeTruck, STATUS } from "../live/fakes";
import { localDayRange } from "../trucks/dateRange";
import { AGE_COLORS, DEFAULT_TILE_URL } from "./mapModel";
import MapPage from "./MapPage";

// Leaflet never runs in jsdom: the page only talks to this small boundary.
const h = vi.hoisted(() => ({
  view: { setTrucks: vi.fn(), setDrone: vi.fn(), fitToData: vi.fn(), destroy: vi.fn() },
  created: [] as { el: HTMLElement; opts: Record<string, unknown> }[],
}));
vi.mock("./leafletView", () => ({
  createMapView: (el: HTMLElement, opts: Record<string, unknown>) => {
    h.created.push({ el, opts });
    return h.view;
  },
}));

const INFO: Info = {
  version: "0.3.0",
  source: { type: "file", name: "demo.mp4", fps: 30 },
  detector: { model: "m.pt", input_size: 960, device: "cpu", gpu: null, classes: ["truck"] },
  auth: "none",
  dashboard: true,
  telemetry: "mavlink",
  map_tile_url: "https://tiles.test/{z}/{x}/{y}.png",
};

const FIX: DronePosition = {
  lat: 12.5,
  lon: 77.5,
  alt_agl_m: 40,
  yaw_deg: 90,
  t_iso: "2026-11-06T06:00:00Z",
};

// 11:33 on 6 Nov 2026 in Asia/Kolkata, so "today" is 2026-11-06 and all trucks below fall in it.
const TODAY = localDayRange("2026-11-06", "2026-11-06");

function located(id: number, over: Partial<Truck> = {}): Truck {
  return makeTruck(id, {
    lat: 12 + id / 1000,
    lon: 77 + id / 1000,
    geo_error_m: 3,
    geo_status: "ok",
    ...over,
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-06T06:03:04Z"));
  h.created.length = 0;
  for (const fn of Object.values(h.view)) fn.mockClear();
});
afterEach(() => {
  vi.useRealTimers();
});

function Gate() {
  const { unauthorized } = useApi();
  return unauthorized ? <TokenPrompt /> : null;
}

interface Setup {
  info?: Info;
  status?: Status;
  /** One page of trucks, by offset. */
  trucks?: (offset: number) => Truck[];
  telemetry?: { state: string; latest: DronePosition | null; track: [number, number][] };
}

function setup(over: Setup = {}) {
  const client = makeClient([], over.status ?? STATUS);
  const c = client as unknown as Record<string, ReturnType<typeof vi.fn>>;
  c.getInfo.mockImplementation(async () => over.info ?? INFO);
  c.listTrucks.mockImplementation(async (q: { offset?: number }) => ({
    trucks: over.trucks ? over.trucks(q.offset ?? 0) : [],
    limit: 200,
    offset: q.offset ?? 0,
  }));
  if (over.telemetry) c.getTelemetry.mockImplementation(async () => over.telemetry);
  const live = makeConnect();
  const view = render(
    <ApiProvider client={client} initialToken="t">
      <MapPage connect={live.connect} />
      <Gate />
    </ApiProvider>,
  );
  return { client, c, live, view };
}

function lastTrucks(): Truck[] {
  return h.view.setTrucks.mock.calls.at(-1)?.[0] ?? [];
}
function lastDrone(): { latest: DronePosition | null; track: [number, number][] } {
  const call = h.view.setDrone.mock.calls.at(-1) ?? [null, []];
  return { latest: call[0], track: call[1] };
}
async function loaded(ids: number[]) {
  await waitFor(() =>
    expect(lastTrucks().map((t) => t.id).sort((a, b) => a - b)).toEqual(ids),
  );
}

test("map shows no-telemetry notice", async () => {
  const { c } = setup({ info: { ...INFO, telemetry: "none" }, trucks: () => [located(1)] });
  expect(await screen.findByText(/No drone position data/)).toHaveTextContent(
    "set telemetry in config.yaml (see README: GPS & map)",
  );
  // trucks located by an older run are still drawn
  await loaded([1]);
  expect(c.getTelemetry).not.toHaveBeenCalled();
});

test("a missing telemetry field means none, and the tile url falls back to OpenStreetMap", async () => {
  const old: Info = { ...INFO };
  delete old.telemetry;
  delete old.map_tile_url;
  setup({ info: old });
  expect(await screen.findByText(/No drone position data/)).toBeInTheDocument();
  await waitFor(() => expect(h.created).toHaveLength(1));
  expect(h.created[0].opts.tileUrl).toBe(DEFAULT_TILE_URL);
  expect(DEFAULT_TILE_URL).toBe("https://tile.openstreetmap.org/{z}/{x}/{y}.png");
});

test("creates the map with the engine's tile url, attribution and media helper", async () => {
  const { client } = setup();
  await waitFor(() => expect(h.created).toHaveLength(1));
  const { el, opts } = h.created[0];
  expect(opts.tileUrl).toBe("https://tiles.test/{z}/{x}/{y}.png");
  expect(opts.attribution).toBe("© OpenStreetMap contributors");
  expect(typeof opts.onTileError).toBe("function");
  expect((opts.mediaUrl as (p: string) => string)("/media/c1.jpg")).toBe(
    client.mediaUrl("/media/c1.jpg"),
  );
  expect(screen.getByRole("region", { name: "Map" })).toBe(el);
});

test("passes every truck to the map and counts the ones it cannot draw", async () => {
  const ok = located(1);
  const rough = located(2, { geo_status: "unreliable", geo_error_m: 90 });
  const noPos = makeTruck(3, { geo_status: "unreliable" });
  const unknown = makeTruck(4, { geo_status: "unknown" });
  setup({ trucks: () => [ok, rough, noPos, unknown] });
  await loaded([1, 2, 3, 4]);
  expect(screen.getByText("2 trucks in this range have no reliable location")).toBeInTheDocument();
  expect(screen.queryByText("No trucks with a location in this range")).not.toBeInTheDocument();
});

test("one unlocated truck reads naturally", async () => {
  setup({ trucks: () => [located(1), makeTruck(2, { geo_status: "unknown" })] });
  expect(
    await screen.findByText("1 truck in this range has no reliable location"),
  ).toBeInTheDocument();
});

test("no located trucks shows its notice, only once loading is done", async () => {
  setup({ trucks: () => [makeTruck(1, { geo_status: "unknown" })] });
  expect(screen.queryByText("No trucks with a location in this range")).not.toBeInTheDocument();
  expect(await screen.findByText("No trucks with a location in this range")).toBeInTheDocument();
});

test("legend shows the three age colours", async () => {
  setup();
  const legend = await screen.findByRole("list", { name: "Marker colours by age" });
  const items = within(legend).getAllByRole("listitem");
  expect(items.map((li) => li.textContent)).toEqual(["Last hour", "Earlier today", "Older"]);
  const swatches = items.map(
    (li) => li.querySelector<HTMLElement>(".map-swatch")!.style.backgroundColor,
  );
  const probe = document.createElement("span");
  const norm = (c: string) => {
    probe.style.backgroundColor = c;
    return probe.style.backgroundColor;
  };
  expect(swatches).toEqual([AGE_COLORS.recent, AGE_COLORS.today, AGE_COLORS.older].map(norm));
});

test("drone track updates from live messages", async () => {
  const { live } = setup({
    telemetry: {
      state: "ok",
      latest: FIX,
      track: [
        [12.4, 77.4],
        [12.5, 77.5],
      ],
    },
  });
  await waitFor(() => expect(lastDrone().latest).toEqual(FIX));
  expect(lastDrone().track).toEqual([
    [12.4, 77.4],
    [12.5, 77.5],
  ]);

  const next = { ...FIX, lat: 12.6, lon: 77.7, t_iso: "2026-11-06T06:00:01Z" };
  act(() => live.send({ type: "telemetry", latest: next }));
  expect(lastDrone().latest).toEqual(next);
  expect(lastDrone().track).toEqual([
    [12.4, 77.4],
    [12.5, 77.5],
    [12.6, 77.7],
  ]);
});

test("a live telemetry message without a position leaves the track alone", async () => {
  const { live } = setup({ telemetry: { state: "ok", latest: FIX, track: [[12.5, 77.5]] } });
  await waitFor(() => expect(lastDrone().latest).toEqual(FIX));
  act(() => live.send({ type: "telemetry", latest: null }));
  expect(lastDrone().track).toEqual([[12.5, 77.5]]);
});

test("the live track keeps at most 600 points", async () => {
  const { live } = setup({ telemetry: { state: "ok", latest: FIX, track: [] } });
  await waitFor(() => expect(lastDrone().latest).toEqual(FIX));
  act(() => {
    for (let i = 0; i < 650; i++) {
      live.send({ type: "telemetry", latest: { ...FIX, lat: 20 + i / 1000 } });
    }
  });
  expect(lastDrone().track).toHaveLength(600);
  expect(lastDrone().track.at(-1)).toEqual([20 + 649 / 1000, 77.5]);
});

test("tile error shows offline notice", async () => {
  setup();
  await waitFor(() => expect(h.created).toHaveLength(1));
  expect(
    screen.queryByText("Map tiles unavailable — showing points only"),
  ).not.toBeInTheDocument();
  const onTileError = h.created[0].opts.onTileError as () => void;
  act(() => {
    onTileError();
    onTileError();
  });
  expect(screen.getAllByText("Map tiles unavailable — showing points only")).toHaveLength(1);
});

test("stale and waiting drone data are called out", async () => {
  const stale = setup({ status: { ...STATUS, telemetry_state: "stale" } });
  expect(await screen.findByText("Drone position data is stale")).toBeInTheDocument();
  stale.view.unmount();

  setup({ status: { ...STATUS, telemetry_state: "waiting" } });
  expect(await screen.findByText("Waiting for drone position…")).toBeInTheDocument();
});

test("a live status message updates the drone notice", async () => {
  const { live } = setup({ status: { ...STATUS, telemetry_state: "ok" } });
  await waitFor(() => expect(h.created).toHaveLength(1));
  expect(screen.queryByText("Drone position data is stale")).not.toBeInTheDocument();
  act(() => live.send({ type: "status", status: { ...STATUS, telemetry_state: "stale" } }));
  expect(screen.getByText("Drone position data is stale")).toBeInTheDocument();
});

test("loads today by default and reloads when the range is applied", async () => {
  const user = userEvent.setup();
  const { c } = setup({ trucks: () => [located(1)] });
  await loaded([1]);
  expect(c.listTrucks).toHaveBeenCalledTimes(1);
  expect(c.listTrucks).toHaveBeenLastCalledWith({ ...TODAY, limit: 200, offset: 0 });
  expect(screen.getByLabelText("From")).toHaveValue("2026-11-06");
  expect(screen.getByLabelText("To")).toHaveValue("2026-11-06");

  c.listTrucks.mockImplementation(async () => ({ trucks: [located(9)], limit: 200, offset: 0 }));
  await user.clear(screen.getByLabelText("From"));
  await user.type(screen.getByLabelText("From"), "2026-11-02");
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await loaded([9]);
  expect(c.listTrucks).toHaveBeenCalledTimes(2);
  expect(c.listTrucks).toHaveBeenLastCalledWith({
    ...localDayRange("2026-11-02", "2026-11-06"),
    limit: 200,
    offset: 0,
  });
});

test("pages through the range 200 at a time", async () => {
  const { c } = setup({
    trucks: (offset) =>
      Array.from({ length: offset === 0 ? 200 : 50 }, (_, i) => located(offset + i + 1)),
  });
  await loaded(Array.from({ length: 250 }, (_, i) => i + 1));
  expect(c.listTrucks.mock.calls.map((call) => call[0].offset)).toEqual([0, 200]);
  expect(screen.queryByText(/Showing the first 1000 trucks/)).not.toBeInTheDocument();
});

test("stops paging at 1000 trucks and says so", async () => {
  const { c } = setup({
    trucks: (offset) => Array.from({ length: 200 }, (_, i) => located(offset + i + 1)),
  });
  await loaded(Array.from({ length: 1000 }, (_, i) => i + 1));
  expect(c.listTrucks.mock.calls.map((call) => call[0].offset)).toEqual([0, 200, 400, 600, 800]);
  expect(screen.getByText("Showing the first 1000 trucks in this range")).toBeInTheDocument();
});

test("live trucks inside the range appear, others are ignored, updates replace", async () => {
  const { live } = setup({ trucks: () => [located(1)] });
  await loaded([1]);
  act(() => live.send({ type: "truck.new", truck: located(2) }));
  await loaded([1, 2]);

  act(() =>
    live.send({ type: "truck.new", truck: located(3, { first_seen: "2026-11-01T00:00:00Z" }) }),
  );
  expect(lastTrucks().map((t) => t.id)).not.toContain(3);

  act(() => live.send({ type: "truck.updated", truck: located(2, { geo_status: "unreliable" }) }));
  expect(lastTrucks().find((t) => t.id === 2)?.geo_status).toBe("unreliable");
  expect(lastTrucks()).toHaveLength(2);
});

test("Fit to data fits the map", async () => {
  const user = userEvent.setup();
  setup({ trucks: () => [located(1)] });
  await loaded([1]);
  const before = h.view.fitToData.mock.calls.length;
  await user.click(screen.getByRole("button", { name: "Fit to data" }));
  expect(h.view.fitToData).toHaveBeenCalledTimes(before + 1);
});

test("fits to the trucks once they have loaded", async () => {
  setup({ trucks: () => [located(1)] });
  await loaded([1]);
  await waitFor(() => expect(h.view.fitToData).toHaveBeenCalled());
});

test("a failed load shows an error and Retry loads again", async () => {
  const user = userEvent.setup();
  const { c } = setup();
  await waitFor(() => expect(c.listTrucks).toHaveBeenCalledTimes(1));
  c.listTrucks.mockReset();
  c.listTrucks.mockRejectedValueOnce(new TypeError("down"));
  c.listTrucks.mockResolvedValue({ trucks: [located(1)], limit: 200, offset: 0 });
  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(await screen.findByText("Could not load trucks.")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Retry" }));
  await loaded([1]);
  expect(screen.queryByText("Could not load trucks.")).not.toBeInTheDocument();
});

test("401 reports unauthorized instead of an error", async () => {
  const { c } = setup();
  await waitFor(() => expect(c.listTrucks).toHaveBeenCalled());
  c.listTrucks.mockRejectedValue(new ApiError(401, "no"));
  await userEvent.setup().click(screen.getByRole("button", { name: "Apply" }));
  expect(await screen.findByText("Access token required")).toBeInTheDocument();
  expect(screen.queryByText("Could not load trucks.")).not.toBeInTheDocument();
});

test("leaving the page destroys the map and closes the live connection", async () => {
  const { live, view } = setup();
  await waitFor(() => expect(h.created).toHaveLength(1));
  view.unmount();
  expect(h.view.destroy).toHaveBeenCalledTimes(1);
  expect(live.stops[0]).toHaveBeenCalled();
});
