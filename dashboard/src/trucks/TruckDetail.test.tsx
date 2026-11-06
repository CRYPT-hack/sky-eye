import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { ApiProvider } from "../apiContext";
import { ApiError } from "../api/client";
import { makeClient, makeTruck } from "../live/fakes";
import type { Truck } from "../api/types";
import TruckDetail from "./TruckDetail";

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
  const { view } = setup(makeTruck(5, { clip_url: null }));
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
  setup(makeTruck(5, { snapshot_url: null }));
  expect(await screen.findByText("No full frame saved")).toBeInTheDocument();
  expect(screen.queryByAltText("Truck #5 full frame")).not.toBeInTheDocument();
});

test("truck not found", async () => {
  setup(new ApiError(404, "nope"));
  expect(await screen.findByText("Truck not found")).toBeInTheDocument();
});
