import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ApiError } from "../api/client";
import type { Info, Runtime } from "../api/types";
import { ApiProvider, useApi } from "../apiContext";
import TokenPrompt from "../components/TokenPrompt";
import { makeClient, STATUS } from "../live/fakes";
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

function setup(over: { info?: Info | Error; runtime?: Runtime } = {}) {
  const runtime = over.runtime ?? RUNTIME;
  const client = makeClient();
  const c = client as unknown as Record<string, ReturnType<typeof vi.fn>>;
  c.getInfo.mockImplementation(async () => {
    if (over.info instanceof Error) throw over.info;
    return over.info ?? INFO;
  });
  c.getRuntime.mockImplementation(async () => runtime);
  c.putRuntime.mockImplementation(async (p: Partial<Runtime>) => ({
    ...runtime,
    ...p,
    ...(p.alerts ? { alerts: { ...runtime.alerts, ...p.alerts } } : {}),
  }));
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
  expect(view.container.textContent).toContain("Running");
  expect(screen.getByText("100")).toBeInTheDocument();
});

const fact = (label: string) => screen.getByText(label, { selector: "dt" }).nextElementSibling;

test("numbers are rounded like the live page, states labelled like it", async () => {
  const { c } = setup({ info: { ...INFO, source: { ...INFO.source, fps: 30000 / 1001 } } });
  c.getStatus.mockResolvedValue({ ...STATUS, fps: 24.5678, source_state: "running" });
  await flush(2000); // the next poll
  expect(fact("FPS")).toHaveTextContent(/^24\.6$/);
  expect(fact("Source")).toHaveTextContent("file · demo.mp4 · 29.97 fps");
  expect(fact("Source state")).toHaveTextContent(/^Running$/);
  c.getStatus.mockResolvedValue({ ...STATUS, fps: 25, source_state: "paused" as never });
  await flush(2000);
  expect(fact("FPS")).toHaveTextContent(/^25\.0$/);
  expect(fact("Source state")).toHaveTextContent(/^paused$/);
});

test("whole-number source fps has no decimals", async () => {
  setup();
  await flush();
  expect(fact("Source")).toHaveTextContent("file · demo.mp4 · 30 fps");
});

test("network failures say the engine cannot be reached, in one banner", async () => {
  const { c } = setup({ info: new TypeError("Failed to fetch") });
  c.getStatus.mockRejectedValue(new TypeError("Failed to fetch"));
  await flush(2000); // the next poll fails too
  const alerts = screen.getAllByRole("alert");
  expect(alerts).toHaveLength(1);
  expect(alerts[0]).toHaveTextContent(/^Cannot reach the engine$/);
  c.getInfo.mockResolvedValue(INFO);
  c.getStatus.mockResolvedValue(STATUS);
  await flush(2000);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("different info and poll errors still share one banner", async () => {
  const { c } = setup({ info: new ApiError(500, "info broke") });
  c.getStatus.mockRejectedValue(new TypeError("Failed to fetch"));
  await flush(2000); // the next poll fails too
  const alerts = screen.getAllByRole("alert");
  expect(alerts).toHaveLength(1);
  expect(alerts[0]).toHaveTextContent("info broke");
  expect(alerts[0]).toHaveTextContent("Cannot reach the engine");
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

test("saving the same token again retries, and asks again if it is still refused", async () => {
  const { c } = setup({ info: new ApiError(401, "unauthorized") });
  await flush();
  expect(c.getInfo).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("Access token"), { target: { value: "t" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" })); // the token it already had
  await flush();
  expect(c.getInfo).toHaveBeenCalledTimes(2);
  expect(screen.getByRole("dialog", { name: "Access token required" })).toBeInTheDocument();
  c.getInfo.mockResolvedValue(INFO);
  fireEvent.change(screen.getByLabelText("Access token"), { target: { value: "t" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await flush();
  expect(c.getInfo).toHaveBeenCalledTimes(3);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByText("0.2.0")).toBeInTheDocument();
});

test("older in-flight save cannot overwrite a newer choice", async () => {
  const { c } = setup();
  let resolve06!: (r: Runtime) => void;
  c.putRuntime.mockImplementationOnce(
    () => new Promise<Runtime>((res) => (resolve06 = res)),
  );
  await flush();
  const slider = screen.getByLabelText("Confidence") as HTMLInputElement;
  fireEvent.change(slider, { target: { value: "0.6" } });
  await flush(400);
  expect(c.putRuntime).toHaveBeenCalledTimes(1);
  fireEvent.change(slider, { target: { value: "0.7" } });
  await act(async () => resolve06({ ...RUNTIME, confidence: 0.6 }));
  expect(Number(slider.value)).toBe(0.7);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  await flush(400);
  expect(c.putRuntime).toHaveBeenLastCalledWith({ confidence: 0.7 });
});

test("revert target is the last confirmed value", async () => {
  const { c } = setup();
  let resolve06!: (r: Runtime) => void;
  c.putRuntime.mockImplementationOnce(
    () => new Promise<Runtime>((res) => (resolve06 = res)),
  );
  c.putRuntime.mockRejectedValueOnce(new ApiError(422, "invalid runtime settings"));
  await flush();
  const slider = screen.getByLabelText("Confidence") as HTMLInputElement;
  fireEvent.change(slider, { target: { value: "0.6" } });
  await flush(400);
  fireEvent.change(slider, { target: { value: "0.7" } });
  await act(async () => resolve06({ ...RUNTIME, confidence: 0.6 }));
  await flush(400);
  expect(screen.getByRole("alert")).toHaveTextContent("invalid runtime settings");
  expect(Number(slider.value)).toBe(0.6);
});

test("poll error clears after a later successful poll", async () => {
  const { c } = setup();
  await flush();
  c.getStatus.mockRejectedValueOnce(new Error("boom"));
  await flush(2000);
  expect(screen.getByRole("alert")).toHaveTextContent("boom");
  await flush(2000);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("failed info load is retried on the next poll", async () => {
  const { c } = setup({ info: new Error("down") });
  await flush();
  expect(screen.getByRole("alert")).toHaveTextContent("down");
  expect(screen.getByLabelText("Confidence")).toBeDisabled();
  c.getInfo.mockResolvedValue(INFO);
  await flush(2000);
  expect(screen.getByText("0.2.0")).toBeInTheDocument();
  expect(screen.getByLabelText("Confidence")).toBeEnabled();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

const ALERT_INFO: Info = { ...INFO, alerts: ["desktop", "telegram"] };
const ALERT_RT: Runtime = { ...RUNTIME, alerts: { desktop: true, telegram: true } };

test("alerts section is hidden without configured channels", async () => {
  setup();
  await flush();
  expect(screen.queryByText("Alerts")).not.toBeInTheDocument();
  cleanup();
  setup({ info: { ...INFO, alerts: [] } });
  await flush();
  expect(screen.queryByText("Alerts")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Send test alert" })).not.toBeInTheDocument();
});

test("alert switches render only configured channels and save", async () => {
  const { c } = setup({ info: ALERT_INFO, runtime: ALERT_RT });
  await flush();
  expect(screen.getAllByRole("switch")).toHaveLength(2);
  const laptop = screen.getByRole("switch", { name: "Laptop" });
  const telegram = screen.getByRole("switch", { name: "Telegram" });
  expect(screen.queryByRole("switch", { name: "MQTT" })).not.toBeInTheDocument();
  expect(laptop).toBeChecked();
  fireEvent.click(telegram);
  expect(telegram).not.toBeChecked(); // immediately
  expect(c.putRuntime).toHaveBeenCalledWith({ alerts: { telegram: false } });
  await flush();
  expect(telegram).not.toBeChecked();
  expect(laptop).toBeChecked();
});

test("channel missing from runtime.alerts shows off", async () => {
  setup({ info: { ...INFO, alerts: ["mqtt"] }, runtime: { ...RUNTIME, alerts: {} } });
  await flush();
  expect(screen.getByRole("switch", { name: "MQTT" })).not.toBeChecked();
});

test("alert switch reverts on error", async () => {
  const { c } = setup({ info: ALERT_INFO, runtime: ALERT_RT });
  c.putRuntime.mockRejectedValueOnce(new ApiError(422, "unknown alert channel: telegram"));
  await flush();
  const telegram = screen.getByRole("switch", { name: "Telegram" });
  fireEvent.click(telegram);
  await flush();
  expect(telegram).toBeChecked();
  expect(screen.getByRole("alert")).toHaveTextContent("unknown alert channel: telegram");
});

test("toggling two channels at once does not clobber either", async () => {
  const { c } = setup({ info: ALERT_INFO, runtime: ALERT_RT });
  let resolveA!: (r: Runtime) => void;
  c.putRuntime.mockImplementationOnce(() => new Promise<Runtime>((res) => (resolveA = res)));
  await flush();
  const laptop = screen.getByRole("switch", { name: "Laptop" });
  const telegram = screen.getByRole("switch", { name: "Telegram" });
  fireEvent.click(laptop);
  fireEvent.click(telegram);
  await flush();
  expect(c.putRuntime).toHaveBeenNthCalledWith(1, { alerts: { desktop: false } });
  expect(c.putRuntime).toHaveBeenNthCalledWith(2, { alerts: { telegram: false } });
  // laptop's (older) response arrives last, carrying the server's view at that time
  await act(async () => resolveA({ ...RUNTIME, alerts: { desktop: false, telegram: true } }));
  expect(laptop).not.toBeChecked();
  expect(telegram).not.toBeChecked();
});

test("an older response for the same channel cannot overwrite a newer choice", async () => {
  const { c } = setup({ info: ALERT_INFO, runtime: ALERT_RT });
  let resolveOff!: (r: Runtime) => void;
  c.putRuntime.mockImplementationOnce(() => new Promise<Runtime>((res) => (resolveOff = res)));
  await flush();
  const telegram = screen.getByRole("switch", { name: "Telegram" });
  fireEvent.click(telegram); // off
  fireEvent.click(telegram); // on again
  await flush();
  await act(async () => resolveOff({ ...RUNTIME, alerts: { telegram: false } }));
  expect(telegram).toBeChecked();
});

test("send test alert shows the channels it was queued for", async () => {
  const { c } = setup({ info: ALERT_INFO, runtime: ALERT_RT });
  let resolve!: (s: string[]) => void;
  c.sendTestAlert.mockImplementationOnce(() => new Promise<string[]>((res) => (resolve = res)));
  await flush();
  const button = screen.getByRole("button", { name: "Send test alert" });
  fireEvent.click(button);
  await flush();
  expect(button).toBeDisabled();
  await act(async () => resolve(["desktop", "telegram"]));
  expect(button).toBeEnabled();
  expect(
    screen.getByText("Test queued for: Laptop, Telegram — check the engine log if nothing arrives."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Sent to/)).not.toBeInTheDocument();
});

test("send test alert with nothing switched on, and with an error", async () => {
  const { c } = setup({ info: ALERT_INFO, runtime: ALERT_RT });
  c.sendTestAlert.mockResolvedValueOnce([]);
  c.sendTestAlert.mockRejectedValueOnce(new ApiError(409, "no alert channels configured"));
  await flush();
  const button = screen.getByRole("button", { name: "Send test alert" });
  fireEvent.click(button);
  await flush();
  expect(screen.getByText("No channel is switched on")).toBeInTheDocument();
  fireEvent.click(button);
  await flush();
  expect(screen.queryByText("No channel is switched on")).not.toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("no alert channels configured");
});
