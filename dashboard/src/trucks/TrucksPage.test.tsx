import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { ApiProvider, useApi } from "../apiContext";
import { ApiError, type ApiClient } from "../api/client";
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

test("a server error shows a retry banner that refetches", async () => {
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

test("load more skips trucks already shown", async () => {
  const errors = vi.spyOn(console, "error").mockImplementation(() => {});
  setup([many(1, 48), many(40, 10)]); // second page repeats ids 40..48
  await waitFor(() => expect(screen.getAllByText(/^Truck #\d+$/)).toHaveLength(48));
  await userEvent.click(screen.getByRole("button", { name: "Load more" }));
  await waitFor(() => expect(screen.getAllByText(/^Truck #\d+$/)).toHaveLength(49));
  expect(screen.getAllByText("Truck #45")).toHaveLength(1);
  expect(errors).not.toHaveBeenCalled();
  errors.mockRestore();
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

test("401 reports unauthorized instead of an error banner", async () => {
  const client = makeClient();
  (client.listTrucks as ReturnType<typeof vi.fn>).mockRejectedValue(new ApiError(401, "no"));
  render(
    <ApiProvider client={client} initialToken="t">
      <TrucksPage />
      <Gate />
    </ApiProvider>,
  );
  expect(await screen.findByText("token prompt")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.queryByText("No trucks saved for this range")).not.toBeInTheDocument();
});

test("saving the same token again refetches each time", async () => {
  const client = makeClient();
  const mock = client.listTrucks as ReturnType<typeof vi.fn>;
  mock.mockRejectedValue(new ApiError(401, "no"));
  render(
    <ApiProvider client={client} initialToken="t">
      <TrucksPage />
      <Gate />
    </ApiProvider>,
  );
  await screen.findByText("token prompt");
  expect(mock).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole("button", { name: "Save token t" }));
  await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
  expect(await screen.findByText("token prompt")).toBeInTheDocument();
  mock.mockResolvedValue({ trucks: [makeTruck(4)], limit: 48, offset: 0 });
  await userEvent.click(screen.getByRole("button", { name: "Save token t" }));
  expect(await screen.findByText("Truck #4")).toBeInTheDocument();
  expect(mock).toHaveBeenCalledTimes(3);
});

describe("gallery survives a detail visit", () => {
  afterEach(() => vi.restoreAllMocks());

  function Shell({ client, page }: { client: ApiClient; page: "list" | "detail" }) {
    return (
      <ApiProvider client={client} initialToken="t">
        {page === "list" ? <TrucksPage /> : <p>detail page</p>}
        <Gate />
      </ApiProvider>
    );
  }

  function filtered() {
    const client = makeClient();
    (client.listTrucks as ReturnType<typeof vi.fn>).mockImplementation(
      async (q: { offset?: number }) => ({
        trucks: q.offset ? many(49, 48) : many(1, 48),
        limit: 48,
        offset: q.offset ?? 0,
      }),
    );
    return client;
  }

  const cards = () => screen.queryAllByText(/^Truck #\d+$/);

  test("keeps the filter, every loaded page and the scroll position", async () => {
    const client = filtered();
    const mock = client.listTrucks as ReturnType<typeof vi.fn>;
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const view = render(<Shell client={client} page="list" />);
    await waitFor(() => expect(cards()).toHaveLength(48));
    await userEvent.type(screen.getByLabelText("From"), "2026-11-06");
    await userEvent.type(screen.getByLabelText("To"), "2026-11-06");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }));
    await waitFor(() => expect(cards()).toHaveLength(96));
    expect(mock).toHaveBeenCalledTimes(3);

    vi.spyOn(window, "scrollY", "get").mockReturnValue(1234);
    view.rerender(<Shell client={client} page="detail" />); // opened a truck
    expect(cards()).toHaveLength(0);
    view.rerender(<Shell client={client} page="list" />); // and came back

    expect(screen.getByLabelText("From")).toHaveValue("2026-11-06");
    expect(screen.getByLabelText("To")).toHaveValue("2026-11-06");
    expect(cards()).toHaveLength(96);
    expect(mock).toHaveBeenCalledTimes(3); // nothing fetched again
    expect(scrollTo).toHaveBeenLastCalledWith(0, 1234);
    expect(screen.queryByText("No trucks saved for this range")).not.toBeInTheDocument();

    // and Load more continues after the restored rows
    mock.mockResolvedValueOnce({ trucks: many(97, 3), limit: 48, offset: 96 });
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(cards()).toHaveLength(99));
    expect(mock).toHaveBeenLastCalledWith({
      limit: 48,
      offset: 96,
      from: "2026-11-05T18:30:00.000Z",
      to: "2026-11-06T18:30:00.000Z",
    });
  });

  test("a new auth epoch resets the saved gallery", async () => {
    const client = filtered();
    const mock = client.listTrucks as ReturnType<typeof vi.fn>;
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const view = render(<Shell client={client} page="list" />);
    await waitFor(() => expect(cards()).toHaveLength(48));
    await userEvent.type(screen.getByLabelText("From"), "2026-11-06");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
    view.rerender(<Shell client={client} page="detail" />);
    await userEvent.click(screen.getByRole("button", { name: "Save token t" }));
    view.rerender(<Shell client={client} page="list" />);
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
    expect(mock).toHaveBeenLastCalledWith({ limit: 48, offset: 0 });
    expect(screen.getByLabelText("From")).toHaveValue("");
    await waitFor(() => expect(cards()).toHaveLength(48));
  });

  test("a new auth epoch while the gallery is open refetches it", async () => {
    const client = filtered();
    const mock = client.listTrucks as ReturnType<typeof vi.fn>;
    render(<Shell client={client} page="list" />);
    await waitFor(() => expect(cards()).toHaveLength(48));
    await userEvent.click(screen.getByRole("button", { name: "Save token t" }));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
  });

  test("Clear resets the saved gallery too", async () => {
    const client = filtered();
    const mock = client.listTrucks as ReturnType<typeof vi.fn>;
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const view = render(<Shell client={client} page="list" />);
    await waitFor(() => expect(cards()).toHaveLength(48));
    await userEvent.type(screen.getByLabelText("From"), "2026-11-06");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(2));
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(mock).toHaveBeenCalledTimes(3));
    expect(mock).toHaveBeenLastCalledWith({ limit: 48, offset: 0 });
    await waitFor(() => expect(cards()).toHaveLength(48));
    view.rerender(<Shell client={client} page="detail" />);
    view.rerender(<Shell client={client} page="list" />);
    expect(screen.getByLabelText("From")).toHaveValue("");
    expect(cards()).toHaveLength(48);
    expect(mock).toHaveBeenCalledTimes(3);
  });

  test("a gallery left while still loading is fetched again", async () => {
    const client = makeClient();
    const mock = client.listTrucks as ReturnType<typeof vi.fn>;
    mock.mockImplementationOnce(() => new Promise(() => {}));
    mock.mockResolvedValue({ trucks: [makeTruck(9)], limit: 48, offset: 0 });
    const view = render(<Shell client={client} page="list" />);
    view.rerender(<Shell client={client} page="detail" />);
    view.rerender(<Shell client={client} page="list" />);
    expect(await screen.findByText("Truck #9")).toBeInTheDocument();
    expect(mock).toHaveBeenCalledTimes(2);
  });
});

test("restores in StrictMode without fetching twice", async () => {
  const client = makeClient();
  const mock = client.listTrucks as ReturnType<typeof vi.fn>;
  mock.mockResolvedValue({ trucks: many(1, 3), limit: 48, offset: 0 });
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  const tree = (page: "list" | "detail") => (
    <StrictMode>
      <ApiProvider client={client} initialToken="t">
        {page === "list" ? <TrucksPage /> : <p>detail page</p>}
      </ApiProvider>
    </StrictMode>
  );
  const view = render(tree("list"));
  await waitFor(() => expect(screen.getAllByText(/^Truck #\d+$/)).toHaveLength(3));
  const calls = mock.mock.calls.length;
  view.rerender(tree("detail"));
  view.rerender(tree("list"));
  await new Promise((r) => setTimeout(r, 20));
  expect(screen.getAllByText(/^Truck #\d+$/)).toHaveLength(3);
  expect(mock.mock.calls).toHaveLength(calls);
  vi.restoreAllMocks();
});
