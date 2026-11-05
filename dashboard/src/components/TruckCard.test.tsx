import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { makeTruck } from "../live/fakes";
import TruckCard from "./TruckCard";

const media = (p: string) => `${p}?token=t`;

test("links to the detail page and shows crop, score and duration", () => {
  render(<TruckCard truck={makeTruck(7)} mediaUrl={media} />);
  const img = screen.getByAltText("Truck #7");
  expect(img).toHaveAttribute("src", "/media/c7.jpg?token=t");
  expect(img).toHaveAttribute("loading", "lazy");
  expect(img.closest("a")).toHaveAttribute("href", "#/trucks/7");
  expect(screen.getByText("80%")).toBeInTheDocument();
  expect(screen.getByText("5 s")).toBeInTheDocument();
  expect(screen.queryByText("clip")).not.toBeInTheDocument();
});

test("shows a clip badge when a clip exists", () => {
  render(<TruckCard truck={makeTruck(7, { clip_url: "/media/v.mp4" })} mediaUrl={media} />);
  expect(screen.getByText("clip")).toBeInTheDocument();
});

test("no crop: no image, still a card", () => {
  render(<TruckCard truck={makeTruck(7, { crop_url: null })} mediaUrl={media} />);
  expect(screen.queryByAltText("Truck #7")).not.toBeInTheDocument();
  expect(screen.getByText("Truck #7")).toBeInTheDocument();
});
