import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ApiError } from "../api/client";
import type { Info, Runtime } from "../api/types";
import { ApiProvider, useApi } from "../apiContext";
import TokenPrompt from "../components/TokenPrompt";
import { makeClient } from "../live/fakes";
import StatusPage from "./StatusPage";

const INFO: Info = {
  version: "0.2.0",
  source: { type: "file", name: "demo.mp4", fps: 30 },
  detector: {
    model: "yolo-trucks.pt",
    input_size: 960,
    device: "cuda",
    gpu: "NVIDIA GeForce RTX 4050 Laptop GPU",
    classes: ["truck"],
  },
  auth: "token",
  dashboard: true,
};
const RUNTIME: Runtime = { confidence: 0.4, sahi_enabled: false };

beforeEach(() => {
  window.localStorage.clear();
  vi.useFakeTimers({
    toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"],
  });
  vi.setSystemTime(new Date("2026-11-06T06:03:04Z"));
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

function Gate() {
  const { unauthorized } = useApi();
  return unauthorized ? <TokenPrompt /> : null;
}

function setup(over: { info?: Info | Error } = {}) {
  const client = makeClient();
  const c = client as unknown as Record<string, ReturnType<typeof vi.fn>>;
  c.getInfo.mockImplementation(async () => {
    if (over.info instanceof Error) throw over.info;
    return over.info ?? INFO;
  });
  c.getRuntime.mockImplementation(async () => RUNTIME);
  c.putRuntime.mockImplementation(async (p: Partial<Runtime>) => ({ ...RUNTIME, ...p }));
  const view = render(
    <ApiProvider client={client} initialToken="t">
      <StatusPage />
      <Gate />
    </ApiProvider>,
  );
  return { client, c, view };
}

async function flush(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

test("shows engine and detector info", async () => {
  const { view } = setup();
  await flush();
  expect(screen.getByText("0.2.0")).toBeInTheDocument();
  expect(screen.getByText(/demo\.mp4/)).toBeInTheDocument();
  expect(screen.getByText("NVIDIA GeForce RTX 4050 Laptop GPU")).toBeInTheDocument();
  expect(screen.getByText("yolo-trucks.pt")).toBeInTheDocument();
  expect(screen.getByText("3 min 4 s")).toBeInTheDocument();
  expect(view.container.textContent).toContain("running");
  expect(screen.getByText("100")).toBeInTheDocument();
});

test("gpu null shows CPU", async () => {
  setup({ info: { ...INFO, detector: { ...INFO.detector, device: "cpu", gpu: null } } });
  await flush();
  expect(screen.getByText("CPU")).toBeInTheDocument();
});

test("uptime ticks every second", async () => {
  setup();
  await flush();
  expect(screen.getByText("3 min 4 s")).toBeInTheDocument();
  await flush(1000);
  expect(screen.getByText("3 min 5 s")).toBeInTheDocument();
});

test("polls status every 2 s and pauses while hidden", async () => {
  const { c } = setup();
  await flush();
  expect(c.getStatus).toHaveBeenCalledTimes(1);
  await flush(2000);
  expect(c.getStatus).toHaveBeenCalledTimes(2);
  let state: DocumentVisibilityState = "hidden";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => state);
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await flush(6000);
  expect(c.getStatus).toHaveBeenCalledTimes(2);
  state = "visible";
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await flush(0);
  expect(c.getStatus).toHaveBeenCalledTimes(3);
});

test("debounces confidence save", async () => {
  const { c } = setup();
  await flush();
  const slider = screen.getByLabelText("Confidence");
  expect(slider).toHaveAttribute("min", "0.05");
  expect(slider).toHaveAttribute("max", "0.95");
  expect(slider).toHaveAttribute("step", "0.05");
  expect(slider).toHaveAttribute("aria-valuetext", "40%");
  fireEvent.change(slider, { target: { value: "0.5" } });
  await flush(300);
  fireEvent.change(slider, { target: { value: "0.55" } });
  await flush(300);
  fireEvent.change(slider, { target: { value: "0.6" } });
  expect(slider).toHaveAttribute("aria-valuetext", "60%");
  expect(screen.getByText("60%")).toBeInTheDocument();
  await flush(399);
  expect(c.putRuntime).not.toHaveBeenCalled();
  await flush(1);
  expect(c.putRuntime).toHaveBeenCalledTimes(1);
  expect(c.putRuntime).toHaveBeenCalledWith({ confidence: 0.6 });
  expect(screen.getByText("Saved")).toBeInTheDocument();
  await flush(3000);
  expect(screen.queryByText("Saved")).not.toBeInTheDocument();
});

test("reverts slider on save error", async () => {
  const { c } = setup();
  c.putRuntime.mockRejectedValue(new ApiError(422, "invalid runtime settings"));
  await flush();
  const slider = screen.getByLabelText("Confidence") as HTMLInputElement;
  fireEvent.change(slider, { target: { value: "0.6" } });
  await flush(400);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid runtime settings");
  expect(Number(slider.value)).toBe(0.4);
  expect(slider).toHaveAttribute("aria-valuetext", "40%");
});

test("pending save is cancelled on unmount", async () => {
  const { c, view } = setup();
  await flush();
  fireEvent.change(screen.getByLabelText("Confidence"), { target: { value: "0.6" } });
  view.unmount();
  await flush(1000);
  expect(c.putRuntime).not.toHaveBeenCalled();
});

test("shows token prompt on 401", async () => {
  setup({ info: new ApiError(401, "unauthorized") });
  await flush();
  expect(screen.getByRole("dialog", { name: "Access token required" })).toBeInTheDocument();
});

test("saving token retries", async () => {
  const { c } = setup({ info: new ApiError(401, "unauthorized") });
  await flush();
  expect(c.getInfo).toHaveBeenCalledTimes(1);
  c.getInfo.mockResolvedValue(INFO);
  fireEvent.change(screen.getByLabelText("Access token"), { target: { value: "secret" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await flush();
  expect(window.localStorage.getItem("skyeye.token")).toBe("secret");
  expect(c.getInfo).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByText("0.2.0")).toBeInTheDocument();
});
