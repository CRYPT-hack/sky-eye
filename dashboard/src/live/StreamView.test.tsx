import { act, fireEvent, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import StreamView from "./StreamView";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

const img = () => screen.getByAltText("Live drone video with truck boxes");

test("renders the stream with alt text", () => {
  render(<StreamView src="/stream.mjpg?token=t" />);
  expect(img()).toHaveAttribute("src", "/stream.mjpg?token=t");
});

test("stream image retries after error with a cache-buster", () => {
  render(<StreamView src="/stream.mjpg?token=t" />);
  fireEvent.error(img());
  expect(screen.getByText("Video stream unavailable — retrying")).toBeInTheDocument();

  act(() => {
    vi.advanceTimersByTime(2000);
  });
  expect(img().getAttribute("src")).toMatch(/^\/stream\.mjpg\?token=t&_=\d+$/);

  fireEvent.load(img());
  expect(screen.queryByText("Video stream unavailable — retrying")).not.toBeInTheDocument();
});

test("cache-buster uses ? when the url has no query", () => {
  render(<StreamView src="/stream.mjpg" />);
  fireEvent.error(img());
  act(() => {
    vi.advanceTimersByTime(2000);
  });
  expect(img().getAttribute("src")).toMatch(/^\/stream\.mjpg\?_=\d+$/);
});

test("reconnects when the live state goes from reconnecting back to open", () => {
  const { rerender } = render(<StreamView src="/stream.mjpg?token=t" liveState="open" />);
  rerender(<StreamView src="/stream.mjpg?token=t" liveState="reconnecting" />);
  expect(img()).toHaveAttribute("src", "/stream.mjpg?token=t");
  rerender(<StreamView src="/stream.mjpg?token=t" liveState="open" />);
  expect(img().getAttribute("src")).toMatch(/&_=\d+$/);
});

test("unmount drops the src so the browser closes the multipart stream", () => {
  const { unmount } = render(<StreamView src="/stream.mjpg?token=t" />);
  const el = img();
  const onError = vi.fn();
  el.addEventListener("error", onError);
  unmount();
  expect(el.hasAttribute("src")).toBe(false);
  expect(onError).not.toHaveBeenCalled();
});

test("hidden tab: no src; visible again: src back with a fresh cache-buster", () => {
  let state: DocumentVisibilityState = "visible";
  const spy = vi.spyOn(document, "visibilityState", "get").mockImplementation(() => state);
  try {
    render(<StreamView src="/stream.mjpg?token=t" />);
    expect(img()).toHaveAttribute("src", "/stream.mjpg?token=t");

    state = "hidden";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(img().hasAttribute("src")).toBe(false);

    state = "visible";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const first = img().getAttribute("src");
    expect(first).toMatch(/^\/stream\.mjpg\?token=t&_=\d+$/);

    state = "hidden";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    state = "visible";
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(img().getAttribute("src")).toMatch(/&_=\d+$/);
    expect(img().getAttribute("src")).not.toBe(first);
  } finally {
    spy.mockRestore();
  }
});

test("starts without a src when the page loads in a hidden tab", () => {
  const spy = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  try {
    render(<StreamView src="/stream.mjpg?token=t" />);
    expect(img().hasAttribute("src")).toBe(false);
  } finally {
    spy.mockRestore();
  }
});

test("a StrictMode effect replay keeps the src", () => {
  render(
    <StrictMode>
      <StreamView src="/stream.mjpg?token=t" />
    </StrictMode>,
  );
  expect(img()).toHaveAttribute("src", "/stream.mjpg?token=t");
});
