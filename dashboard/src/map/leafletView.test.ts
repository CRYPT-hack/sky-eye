import { expect, test } from "vitest";
import { makeTruck } from "../live/fakes";
import { popupContent } from "./leafletView";

test("popup shows the crop through mediaUrl, local time, score and a link to the truck", () => {
  const first = new Date(2026, 10, 3, 14, 5, 9);
  const truck = makeTruck(7, {
    first_seen: first.toISOString(),
    best_score: 0.87,
    crop_url: "/media/c7.jpg",
  });
  const el = popupContent(truck, (p) => `${p}?token=abc`);

  const img = el.querySelector("img")!;
  expect(img.getAttribute("src")).toBe("/media/c7.jpg?token=abc");
  expect(img.alt).toBe("Truck #7 close-up");
  expect(el.querySelector("p")!.textContent).toBe("3 Nov 14:05 · 87%");
  const link = el.querySelector("a")!;
  expect(link.getAttribute("href")).toBe("#/trucks/7");
  expect(link.textContent).toBe("Truck #7");
});

test("popup without a crop has no image", () => {
  const el = popupContent(makeTruck(8, { crop_url: null }), (p) => p);
  expect(el.querySelector("img")).toBeNull();
  expect(el.querySelector("a")).not.toBeNull();
});
