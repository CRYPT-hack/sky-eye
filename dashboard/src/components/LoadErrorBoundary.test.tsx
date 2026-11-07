import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { lazy, Suspense } from "react";
import { expect, test, vi } from "vitest";
import LoadErrorBoundary from "./LoadErrorBoundary";

test("shows a message and a Reload button when a lazy page fails to load", async () => {
  const log = vi.spyOn(console, "error").mockImplementation(() => {});
  const reload = vi.fn();
  const Broken = lazy(() =>
    Promise.reject<{ default: () => null }>(new Error("Failed to fetch dynamically imported module")),
  );
  render(
    <LoadErrorBoundary message="Could not load the map — reload the page" reload={reload}>
      <Suspense fallback="loading">
        <Broken />
      </Suspense>
    </LoadErrorBoundary>,
  );
  expect(await screen.findByText("Could not load the map — reload the page")).toBeInTheDocument();
  await userEvent.setup().click(screen.getByRole("button", { name: "Reload" }));
  expect(reload).toHaveBeenCalledTimes(1);
  log.mockRestore();
});

test("renders its children when nothing fails", () => {
  render(
    <LoadErrorBoundary message="x">
      <span>fine</span>
    </LoadErrorBoundary>,
  );
  expect(screen.getByText("fine")).toBeInTheDocument();
});
