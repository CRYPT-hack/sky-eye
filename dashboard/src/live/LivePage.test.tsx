import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { ApiProvider } from "../apiContext";
import { makeClient, makeConnect, makeTruck, STATUS } from "./fakes";
import LivePage from "./LivePage";

const beepMock = vi.hoisted(() => vi.fn());
const unlockMock = vi.hoisted(() => vi.fn());
vi.mock("./sound", async (orig) => ({
  ...(await orig<typeof import("./sound")>()),
  beep: beepMock,
  unlockAudio: unlockMock,
}));

beforeEach(() => {
  beepMock.mockClear();
  unlockMock.mockClear();
  window.localStorage.clear();
});

async function setup(trucks = [makeTruck(1)]) {
  const client = makeClient(trucks);
  const fake = makeConnect();
  render(
    <ApiProvider client={client}>
      <LivePage connect={fake.connect} />
    </ApiProvider>,
  );
  await waitFor(() => expect(screen.getAllByText(/^Truck #\d+$/).length).toBe(trucks.length));
  return { client, fake };
}

test("shows one card per truck, with the updated score", async () => {
  const { fake } = await setup();
  act(() => fake.send({ type: "truck.new", truck: makeTruck(1) }));
  act(() => fake.send({ type: "truck.updated", truck: makeTruck(1, { best_score: 0.95 }) }));
  expect(screen.getAllByText("Truck #1")).toHaveLength(1);
  expect(screen.getByText("95%")).toBeInTheDocument();
});

test("shows stat tiles from status messages", async () => {
  const { fake } = await setup();
  act(() =>
    fake.send({
      type: "status",
      status: { ...STATUS, trucks_now: 2, trucks_total: 7, fps: 28.46 },
    }),
  );
  const tile = (label: string) => screen.getByText(label).closest(".stat") as HTMLElement;
  expect(within(tile("Trucks now")).getByText("2")).toBeInTheDocument();
  expect(within(tile("Total this run")).getByText("7")).toBeInTheDocument();
  expect(within(tile("FPS")).getByText("28.5")).toBeInTheDocument();
});

test("source tile labels the state, and shows an unknown one as it is", async () => {
  const { fake } = await setup();
  const tile = () => screen.getByText("Source").closest(".stat") as HTMLElement;
  act(() => fake.send({ type: "status", status: { ...STATUS, source_state: "starting" } }));
  expect(within(tile()).getByText("Starting")).toBeInTheDocument();
  act(() =>
    fake.send({ type: "status", status: { ...STATUS, source_state: "paused" as never } }),
  );
  expect(within(tile()).getByText("paused")).toBeInTheDocument();
});

test("ended video banner", async () => {
  const { fake } = await setup();
  act(() => fake.send({ type: "status", status: { ...STATUS, source_state: "ended" } }));
  expect(screen.getByText("Video finished — trucks stay browsable")).toBeInTheDocument();
});

test("source error banner is an alert", async () => {
  const { fake } = await setup();
  act(() => fake.send({ type: "status", status: { ...STATUS, source_state: "error" } }));
  expect(screen.getByRole("alert")).toHaveTextContent("Video source error — check the engine log");
});

test("reconnecting banner", async () => {
  const { fake } = await setup();
  act(() => fake.state("reconnecting"));
  expect(screen.getByText("Reconnecting to the engine…")).toBeInTheDocument();
  act(() => fake.state("open"));
  expect(screen.queryByText("Reconnecting to the engine…")).not.toBeInTheDocument();
});

test("stream image carries the client stream url", async () => {
  await setup();
  expect(screen.getByAltText("Live drone video with truck boxes")).toHaveAttribute(
    "src",
    "/stream.mjpg?token=t",
  );
});

test("new truck card is highlighted, existing one is not", async () => {
  const { fake } = await setup();
  act(() => fake.send({ type: "truck.new", truck: makeTruck(2) }));
  const card = (id: number) => screen.getByText(`Truck #${id}`).closest("a")!;
  expect(card(2)).toHaveClass("is-new");
  expect(card(1)).not.toHaveClass("is-new");
});

test("beeps on truck.new only when sound is on", async () => {
  const user = userEvent.setup();
  const { fake } = await setup();
  act(() => fake.send({ type: "truck.new", truck: makeTruck(2) }));
  expect(beepMock).not.toHaveBeenCalled();

  const toggle = screen.getByRole("button", { name: /sound/i });
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  await user.click(toggle);
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  expect(window.localStorage.getItem("skyeye.sound")).toBe("1");
  expect(unlockMock).toHaveBeenCalledTimes(1);
  expect(beepMock).toHaveBeenCalledTimes(1); // confirmation beep
  beepMock.mockClear();

  act(() => fake.send({ type: "truck.new", truck: makeTruck(3) }));
  expect(beepMock).toHaveBeenCalledTimes(1);
  act(() => fake.send({ type: "truck.updated", truck: makeTruck(3) }));
  act(() => fake.send({ type: "truck.new", truck: makeTruck(3) }));
  expect(beepMock).toHaveBeenCalledTimes(1);
});

test("stream error shows the retry message", async () => {
  await setup();
  fireEvent.error(screen.getByAltText("Live drone video with truck boxes"));
  expect(screen.getByText("Video stream unavailable — retrying")).toBeInTheDocument();
});

test("sound already on at load: first user interaction unlocks audio once", async () => {
  window.localStorage.setItem("skyeye.sound", "1");
  await setup();
  expect(unlockMock).not.toHaveBeenCalled();
  fireEvent.pointerDown(window);
  expect(unlockMock).toHaveBeenCalledTimes(1);
  fireEvent.keyDown(window, { key: "a" });
  fireEvent.pointerDown(window);
  expect(unlockMock).toHaveBeenCalledTimes(1);
});

test("sound off at load: no unlock listener", async () => {
  await setup();
  fireEvent.pointerDown(window);
  expect(unlockMock).not.toHaveBeenCalled();
});
