import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { makeTruck } from "../live/fakes";
import TruckCard from "./TruckCard";

const media = (p: string) => `${p}?token=t`;

test("links to the detail page and shows crop, score and duration", () => {
  const { container } = render(<TruckCard truck={makeTruck(7)} mediaUrl={media} />);
  const img = container.querySelector("img")!;
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
  const { container } = render(
    <TruckCard truck={makeTruck(7, { crop_url: null })} mediaUrl={media} />,
  );
  expect(container.querySelector("img")).toBeNull();
  expect(screen.getByText("Truck #7")).toBeInTheDocument();
});

test("the crop is decorative: the card title alone names the truck", () => {
  const { container } = render(<TruckCard truck={makeTruck(7)} mediaUrl={media} />);
  expect(container.querySelector("img")).toHaveAttribute("alt", "");
  const link = screen.getByRole("link", {
    name: (name) => name.startsWith("Truck #7") && !name.includes("Truck #7", 1),
  });
  expect(link).toHaveAttribute("href", "#/trucks/7");
});
