import { act, fireEvent, render, screen } from "@testing-library/react";
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
