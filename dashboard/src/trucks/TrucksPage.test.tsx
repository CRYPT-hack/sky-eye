import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { ApiProvider } from "../apiContext";
import { ApiError } from "../api/client";
import { makeClient, makeTruck } from "../live/fakes";
import type { Truck } from "../api/types";
import TrucksPage from "./TrucksPage";

const many = (from: number, n: number): Truck[] =>
  Array.from({ length: n }, (_, i) => makeTruck(from + i));

function setup(pages: Truck[][]) {
  const client = makeClient();
  let i = 0;
  (client.listTrucks as ReturnType<typeof vi.fn>).mockImplementation(async () => ({
    trucks: pages[Math.min(i++, pages.length - 1)],
    limit: 48,
    offset: 0,
  }));
  render(
    <ApiProvider client={client}>
      <TrucksPage />
    </ApiProvider>,
  );
  return client;
}

test("loads first page and more", async () => {
  const client = setup([many(1, 48), many(49, 10)]);
  await waitFor(() => expect(screen.getAllByText(/^Truck #\d+$/)).toHaveLength(48));
  expect(client.listTrucks).toHaveBeenCalledWith({ limit: 48, offset: 0 });
  await userEvent.click(screen.getByRole("button", { name: "Load more" }));
  await waitFor(() => expect(screen.getAllByText(/^Truck #\d+$/)).toHaveLength(58));
  expect(client.listTrucks).toHaveBeenLastCalledWith({ limit: 48, offset: 48 });
  expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
});

test("applies date filter", async () => {
  const client = setup([many(1, 48), many(100, 2)]);
  await waitFor(() => expect(screen.getAllByText(/^Truck #\d+$/)).toHaveLength(48));
  await userEvent.click(screen.getByRole("button", { name: "Load more" }));
  await userEvent.type(screen.getByLabelText("From"), "2026-11-06");
  await userEvent.type(screen.getByLabelText("To"), "2026-11-06");
  await userEvent.click(screen.getByRole("button", { name: "Apply" }));
  await waitFor(() =>
    expect(client.listTrucks).toHaveBeenLastCalledWith({
      limit: 48,
      offset: 0,
      from: "2026-11-05T18:30:00.000Z",
      to: "2026-11-06T18:30:00.000Z",
    }),
  );
  await waitFor(() => expect(screen.getAllByText(/^Truck #\d+$/)).toHaveLength(2));
});

test("empty state", async () => {
  setup([[]]);
  expect(await screen.findByText("No trucks saved for this range")).toBeInTheDocument();
});

test("ignores a stale response after the filter changes", async () => {
  const client = makeClient();
  let resolveFirst!: (v: { trucks: Truck[]; limit: number; offset: number }) => void;
  const mock = client.listTrucks as ReturnType<typeof vi.fn>;
  mock.mockImplementationOnce(() => new Promise((r) => (resolveFirst = r)));
  mock.mockImplementation(async () => ({ trucks: [makeTruck(7)], limit: 48, offset: 0 }));
  render(
    <ApiProvider client={client}>
      <TrucksPage />
    </ApiProvider>,
  );
  await userEvent.click(screen.getByRole("button", { name: "Clear" }));
  await screen.findByText("Truck #7");
  resolveFirst({ trucks: [makeTruck(1)], limit: 48, offset: 0 });
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.queryByText("Truck #1")).not.toBeInTheDocument();
  expect(screen.getByText("Truck #7")).toBeInTheDocument();
});

test("401 reports unauthorized; other errors retry", async () => {
  const client = makeClient();
  const mock = client.listTrucks as ReturnType<typeof vi.fn>;
  mock.mockRejectedValueOnce(new ApiError(500, "boom"));
  mock.mockResolvedValue({ trucks: [makeTruck(3)], limit: 48, offset: 0 });
  render(
    <ApiProvider client={client}>
      <TrucksPage />
    </ApiProvider>,
  );
  expect(await screen.findByRole("alert")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("Truck #3")).toBeInTheDocument();
});
