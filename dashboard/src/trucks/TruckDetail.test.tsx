import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";
import { ApiProvider, useApi } from "../apiContext";
import { ApiError } from "../api/client";
import { makeClient, makeTruck } from "../live/fakes";
import type { Truck } from "../api/types";
import TruckDetail from "./TruckDetail";

// Long finished: the engine is no longer working on a clip for it.
const OLD = { first_seen: "2026-01-01T00:00:00Z", last_seen: "2026-01-01T00:00:12Z" };

function setup(result: Truck | Error) {
  const client = makeClient();
  (client.getTruck as ReturnType<typeof vi.fn>).mockImplementation(async () => {
    if (result instanceof Error) throw result;
    return result;
  });
  const view = render(
    <ApiProvider client={client} initialToken="t">
      <TruckDetail id={5} />
    </ApiProvider>,
  );
  return { client, view };
}

test("shows no-clip message", async () => {
  const { view } = setup(makeTruck(5, { clip_url: null, ...OLD }));
  expect(await screen.findByText("No clip saved for this truck")).toBeInTheDocument();
  expect(view.container.querySelector("video")).toBeNull();
  expect(screen.getByAltText("Truck #5 full frame")).toBeInTheDocument();
  expect(screen.getByText("Unknown")).toBeInTheDocument();
});

test("video uses token url", async () => {
  const { view } = setup(makeTruck(5, { clip_url: "/media/clip5.mp4" }));
  await screen.findByAltText("Truck #5 full frame");
  const video = view.container.querySelector("video")!;
  expect(video.getAttribute("src")).toMatch(/[?&]token=t$/);
  expect(video).toHaveAttribute("controls");
});

test("missing snapshot shows placeholder", async () => {
  setup(makeTruck(5, { snapshot_url: null, ...OLD }));
  expect(await screen.findByText("No full frame saved")).toBeInTheDocument();
  expect(screen.queryByAltText("Truck #5 full frame")).not.toBeInTheDocument();
});

test("truck not found", async () => {
  setup(new ApiError(404, "nope"));
  expect(await screen.findByText("Truck not found")).toBeInTheDocument();
});

function Gate() {
  const { unauthorized, setToken } = useApi();
  return (
    <>
      {unauthorized && <p>token prompt</p>}
      <button type="button" onClick={() => setToken("t")}>
        Save token t
      </button>
    </>
  );
}

test("401 reports unauthorized instead of an error", async () => {
  const client = makeClient();
  (client.getTruck as ReturnType<typeof vi.fn>).mockRejectedValue(new ApiError(401, "no"));
  render(
    <ApiProvider client={client} initialToken="t">
      <TruckDetail id={5} />
      <Gate />
    </ApiProvider>,
  );
  expect(await screen.findByText("token prompt")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText("Truck not found")).not.toBeInTheDocument();
});

test("saving the same token again refetches each time", async () => {
  const client = makeClient();
  const mock = client.getTruck as ReturnType<typeof vi.fn>;
  mock.mockRejectedValue(new ApiError(401, "no"));
  render(
    <ApiProvider client={client} initialToken="t">
      <TruckDetail id={5} />
      <Gate />
    </ApiProvider>,
  );
  await screen.findByText("token prompt");
  await userEvent.click(screen.getByRole("button", { name: "Save token t" }));
  await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
  mock.mockResolvedValue(makeTruck(5, { clip_url: "/media/clip5.mp4" }));
  await userEvent.click(screen.getByRole("button", { name: "Save token t" }));
  expect(await screen.findByAltText("Truck #5 full frame")).toBeInTheDocument();
  expect(mock).toHaveBeenCalledTimes(3);
});

test("shows the close-up crop first, then the full frame, both with the token", async () => {
  setup(makeTruck(5, { ...OLD }));
  const crop = await screen.findByAltText("Truck #5 close-up");
  const full = screen.getByAltText("Truck #5 full frame");
  expect(crop.getAttribute("src")).toBe("/media/c5.jpg?token=t");
  expect(full.getAttribute("src")).toBe("/media/s5.jpg?token=t");
  expect(crop.compareDocumentPosition(full) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("no crop saved: just the full frame", async () => {
  setup(makeTruck(5, { crop_url: null, ...OLD }));
  expect(await screen.findByAltText("Truck #5 full frame")).toBeInTheDocument();
  expect(screen.queryByAltText("Truck #5 close-up")).not.toBeInTheDocument();
});

describe("clip still being saved", () => {
  // makeTruck(5): first seen 06:00:05Z, last seen 06:00:10Z
  const NOW = new Date("2026-11-06T06:00:20Z");

  afterEach(() => vi.useRealTimers());

  async function flush(ms = 0) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  function start(...responses: Truck[]) {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    vi.setSystemTime(NOW);
    const client = makeClient();
    const mock = client.getTruck as ReturnType<typeof vi.fn>;
    let i = 0;
    mock.mockImplementation(async () => responses[Math.min(i++, responses.length - 1)]);
    const view = render(
      <ApiProvider client={client} initialToken="t">
        <TruckDetail id={5} />
      </ApiProvider>,
    );
    return { mock, view, client };
  }

  test("a recent truck without a clip polls until the clip is there", async () => {
    const { mock, view } = start(
      makeTruck(5),
      makeTruck(5, { last_seen: "2026-11-06T06:00:25Z", clip_url: "/media/clip5.mp4" }),
    );
    await flush();
    expect(screen.getByRole("status")).toHaveTextContent("Clip is being saved…");
    expect(screen.queryByText("No clip saved for this truck")).not.toBeInTheDocument();
    expect(screen.getByText("5 s")).toBeInTheDocument(); // on screen for, so far
    expect(view.container.querySelector("video")).toBeNull();

    await flush(2999);
    expect(mock).toHaveBeenCalledTimes(1);
    await flush(1);
    expect(mock).toHaveBeenCalledTimes(2);
    const video = view.container.querySelector("video")!;
    expect(video.getAttribute("src")).toBe("/media/clip5.mp4?token=t");
    expect(screen.queryByText("Clip is being saved…")).not.toBeInTheDocument();
    expect(screen.getByText("20 s")).toBeInTheDocument(); // last_seen refreshed

    await flush(10_000);
    expect(mock).toHaveBeenCalledTimes(2); // done: no more polling
  });

  test("an old truck without a clip says so at once and does not poll", async () => {
    const { mock } = start(makeTruck(5, { last_seen: "2026-11-06T05:58:19Z" })); // 121 s ago
    await flush();
    expect(screen.getByText("No clip saved for this truck")).toBeInTheDocument();
    expect(screen.queryByText("Clip is being saved…")).not.toBeInTheDocument();
    await flush(10_000);
    expect(mock).toHaveBeenCalledTimes(1);
  });

  test("gives up 120 s after last_seen", async () => {
    const { mock } = start(makeTruck(5, { last_seen: "2026-11-06T05:58:25Z" })); // 115 s ago
    await flush();
    expect(screen.getByText("Clip is being saved…")).toBeInTheDocument();
    await flush(3000); // 118 s
    expect(mock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Clip is being saved…")).toBeInTheDocument();
    await flush(3000); // 121 s
    expect(mock).toHaveBeenCalledTimes(3);
    expect(screen.getByText("No clip saved for this truck")).toBeInTheDocument();
    await flush(10_000);
    expect(mock).toHaveBeenCalledTimes(3);
  });

  test("a failed re-check keeps the truck and tries again", async () => {
    const { mock, view } = start(makeTruck(5)); // the first fetch already ran
    mock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    mock.mockResolvedValueOnce(makeTruck(5, { clip_url: "/media/clip5.mp4" }));
    await flush();
    await flush(3000);
    expect(mock).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Clip is being saved…")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await flush(3000);
    expect(view.container.querySelector("video")).not.toBeNull();
  });

  test("stops polling when the id changes and on unmount", async () => {
    const { mock, view, client } = start(makeTruck(5));
    await flush();
    view.rerender(
      <ApiProvider client={client} initialToken="t">
        <TruckDetail id={6} />
      </ApiProvider>,
    );
    await flush(10_000);
    expect(mock.mock.calls.filter(([id]) => id === 5)).toHaveLength(1);
    expect(mock.mock.calls.filter(([id]) => id === 6).length).toBeGreaterThan(1);
    view.unmount();
    const calls = mock.mock.calls.length;
    await flush(10_000);
    expect(mock.mock.calls).toHaveLength(calls);
  });
});

describe("clip_status from the engine", () => {
  const NOW = new Date("2026-11-06T06:00:20Z");
  afterEach(() => vi.useRealTimers());

  async function flush(ms = 0) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  function start(...responses: Truck[]) {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
    vi.setSystemTime(NOW);
    const client = makeClient();
    const mock = client.getTruck as ReturnType<typeof vi.fn>;
    let i = 0;
    mock.mockImplementation(async () => responses[Math.min(i++, responses.length - 1)]);
    const view = render(
      <ApiProvider client={client} initialToken="t">
        <TruckDetail id={5} />
      </ApiProvider>,
    );
    return { mock, view };
  }

  test("clip status messages", async () => {
    const cases: [Truck["clip_status"], string][] = [
      ["pending", "Clip is being saved…"],
      ["skipped", "No clip: too many trucks at once"],
      ["failed", "Clip could not be saved"],
      ["removed", "Clip removed to free disk space"],
    ];
    for (const [clip_status, text] of cases) {
      const { view } = start(makeTruck(5, { clip_status, ...OLD }));
      await flush();
      expect(screen.getByText(text)).toBeInTheDocument();
      expect(screen.queryByText("No clip saved for this truck")).not.toBeInTheDocument();
      expect(view.container.querySelector("video")).toBeNull();
      view.unmount();
    }
  });

  test("saved shows the player", async () => {
    const { view } = start(
      makeTruck(5, { clip_status: "saved", clip_url: "/media/clip5.mp4", ...OLD }),
    );
    await flush();
    expect(view.container.querySelector("video")!.getAttribute("src")).toBe(
      "/media/clip5.mp4?token=t",
    );
  });

  test("pending on an old truck still polls, well past 120 s, until saved", async () => {
    const { mock, view } = start(
      makeTruck(5, { clip_status: "pending", ...OLD }),
      makeTruck(5, { clip_status: "pending", ...OLD }),
      makeTruck(5, { clip_status: "saved", clip_url: "/media/clip5.mp4", ...OLD }),
    );
    await flush(200_000);
    expect(mock).toHaveBeenCalledTimes(3);
    expect(view.container.querySelector("video")).not.toBeNull();
  });

  test("pending keeps polling for 10 minutes, then says the status is unknown", async () => {
    const { mock } = start(makeTruck(5, { clip_status: "pending", ...OLD }));
    await flush(599_000);
    expect(screen.getByText("Clip is being saved…")).toBeInTheDocument();
    await flush(3000);
    expect(screen.getByText("Clip status unknown")).toBeInTheDocument();
    const calls = mock.mock.calls.length;
    await flush(30_000);
    expect(mock).toHaveBeenCalledTimes(calls);
  });
});
