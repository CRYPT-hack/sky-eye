import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";

beforeEach(() => {
  window.location.hash = "";
  // The live page talks to the engine; keep these tests off the network.
  vi.stubGlobal("fetch", () => new Promise(() => {}));
  vi.stubGlobal(
    "WebSocket",
    class {
      close() {}
    },
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function navLink(name: string) {
  return screen.getByRole("link", { name });
}

test("nav marks the active page", () => {
  window.location.hash = "#/trucks";
  render(<App />);

  expect(navLink("Trucks")).toHaveAttribute("aria-current", "page");
  expect(navLink("Live")).not.toHaveAttribute("aria-current");
  expect(navLink("Status")).not.toHaveAttribute("aria-current");
});

test("header shows the product name and the three nav links", () => {
  render(<App />);

  expect(screen.getByRole("banner")).toHaveTextContent("sky-eye");
  const nav = screen.getByRole("navigation");
  expect(nav).toContainElement(navLink("Live"));
  expect(navLink("Live")).toHaveAttribute("href", "#/live");
  expect(navLink("Trucks")).toHaveAttribute("href", "#/trucks");
  expect(navLink("Status")).toHaveAttribute("href", "#/status");
});

test("empty hash shows the live page", () => {
  render(<App />);

  expect(navLink("Live")).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("heading", { level: 1, name: "Live" })).toBeInTheDocument();
});

test("truck detail keeps Trucks marked as the current section", () => {
  window.location.hash = "#/trucks/12";
  render(<App />);

  expect(screen.getByRole("heading", { level: 1, name: "Truck #12" })).toBeInTheDocument();
  expect(navLink("Trucks")).toHaveAttribute("aria-current");
  expect(navLink("Live")).not.toHaveAttribute("aria-current");
});

test("page follows hash changes", () => {
  render(<App />);

  act(() => {
    window.location.hash = "#/status";
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

  expect(screen.getByRole("heading", { level: 1, name: "Status" })).toBeInTheDocument();
  expect(navLink("Status")).toHaveAttribute("aria-current", "page");
  expect(document.title).toBe("Status – sky-eye");
});

test("clicking a nav link switches the page", async () => {
  const user = userEvent.setup();
  render(<App />);

  await user.click(navLink("Trucks"));

  await waitFor(() => expect(navLink("Trucks")).toHaveAttribute("aria-current", "page"));
  expect(screen.getByRole("heading", { level: 1, name: "Trucks" })).toBeInTheDocument();
});
