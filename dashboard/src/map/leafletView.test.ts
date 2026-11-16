import { beforeEach, describe, expect, test, vi } from "vitest";
import { makeTruck } from "../live/fakes";
import { createMapView, popupContent } from "./leafletView";
import { AGE_COLORS, ATTRIBUTION_URL, DEFAULT_TILE_URL } from "./mapModel";

// A stand-in for the bits of Leaflet the view uses: it records what was drawn, with
// which options, and what happened to it afterwards.
const h = vi.hoisted(() => {
  type Handler = () => void;
  class FakeLayer {
    readonly kind: string;
    at: unknown;
    readonly options: Record<string, unknown>;
    children: FakeLayer[] = [];
    popup: (() => HTMLElement) | null = null;
    parent: FakeLayer | null = null;
    removed = 0;
    readonly styles: Record<string, unknown>[] = [];
    private readonly handlers = new Map<string, Handler[]>();

    constructor(kind: string, at: unknown, options: Record<string, unknown> = {}) {
      this.kind = kind;
      this.at = at;
      this.options = { ...options };
      layers.push(this);
    }
    addTo(parent: FakeLayer | object) {
      this.parent = parent instanceof FakeLayer ? parent : null;
      return this;
    }
    remove() {
      this.removed += 1;
      return this;
    }
    bindPopup(content: () => HTMLElement) {
      this.popup = content;
      return this;
    }
    bindTooltip() {
      return this;
    }
    setStyle(style: Record<string, unknown>) {
      this.styles.push(style);
      Object.assign(this.options, style);
      return this;
    }
    setLatLng(at: unknown) {
      this.at = at;
      return this;
    }
    setLatLngs(at: unknown) {
      this.at = at;
      return this;
    }
    on(event: string, fn: Handler) {
      this.handlers.set(event, [...(this.handlers.get(event) ?? []), fn]);
      return this;
    }
    fire(event: string) {
      for (const fn of this.handlers.get(event) ?? []) fn();
    }
    getBounds() {
      return {};
    }
  }
  const layers: FakeLayer[] = [];
  const panes = new Map<string, HTMLElement>();
  const map = {
    setView() {
      return map;
    },
    attributionControl: { setPrefix() {} },
    createPane(name: string) {
      const el = document.createElement("div");
      panes.set(name, el);
      return el;
    },
    fitBounds() {},
    remove() {},
  };
  return { FakeLayer, layers, panes, map };
});

vi.mock("leaflet", () => ({
  map: () => h.map,
  tileLayer: (url: string, options: Record<string, unknown>) => new h.FakeLayer("tiles", url, options),
  featureGroup: (children: InstanceType<typeof h.FakeLayer>[] = []) => {
    const group = new h.FakeLayer("group", null);
    group.children = children;
    return group;
  },
  polyline: (at: unknown, options: Record<string, unknown>) => new h.FakeLayer("polyline", at, options),
  circleMarker: (at: unknown, options: Record<string, unknown>) =>
    new h.FakeLayer("circleMarker", at, options),
  circle: (at: unknown, options: Record<string, unknown>) => new h.FakeLayer("circle", at, options),
  latLng: (lat: number, lon: number) => [lat, lon],
  latLngBounds: () => ({ extend() {}, isValid: () => false }),
}));

// Local clock times, so the age buckets hold in any time zone.
const NOW = new Date(2026, 10, 6, 11, 33, 0);
const RECENT = new Date(2026, 10, 6, 11, 20, 0).toISOString();

function ok(id: number, over: Parameters<typeof makeTruck>[1] = {}) {
  return makeTruck(id, {
    lat: 12 + id / 100,
    lon: 77 + id / 100,
    geo_error_m: 42,
    geo_status: "ok",
    first_seen: RECENT,
    ...over,
  });
}

function rough(id: number) {
  return makeTruck(id, {
    lat: 13,
    lon: 78,
    geo_error_m: 250,
    geo_status: "unreliable",
    first_seen: RECENT,
  });
}

const onTileError = vi.fn();
const onTileLoad = vi.fn();

function makeView(tileUrl = DEFAULT_TILE_URL, attribution = "© OpenStreetMap contributors") {
  const view = createMapView(document.createElement("div"), {
    tileUrl,
    attribution,
    onTileError,
    onTileLoad,
    mediaUrl: (p) => p,
  });
  const [truckLayer] = h.layers.filter((l) => l.kind === "group");
  return { view, truckLayer };
}

const of = (kind: string) => h.layers.filter((l) => l.kind === kind);

beforeEach(() => {
  h.layers.length = 0;
  h.panes.clear();
  onTileError.mockClear();
  onTileLoad.mockClear();
});

describe("trucks", () => {
  test("a reliable truck is a point with its error circle underneath, in its age colour", () => {
    const { view, truckLayer } = makeView();
    view.setTrucks([ok(1)], NOW);

    const [dot] = of("circleMarker");
    expect(dot.at).toEqual([12.01, 77.01]);
    expect(dot.options).toMatchObject({ radius: 8, fillColor: AGE_COLORS.recent });
    expect(dot.popup).not.toBeNull();

    const [ring] = of("circle");
    expect(ring.at).toEqual([12.01, 77.01]);
    expect(ring.options).toMatchObject({
      radius: 42,
      color: AGE_COLORS.recent,
      fillColor: AGE_COLORS.recent,
      interactive: false,
    });
    expect(ring.options.dashArray).toBeUndefined();
    expect((ring.options.fillOpacity as number) <= 0.2).toBe(true);
    expect((ring.options.weight as number) <= 1).toBe(true);
    // under every point: a pane of its own, below the markers'
    const pane = h.panes.get(ring.options.pane as string);
    expect(pane).toBeDefined();
    expect(Number(pane!.style.zIndex)).toBeLessThan(400);

    const group = of("group").find((g) => g.children.includes(dot))!;
    expect(group.children).toEqual([ring, dot]);
    expect(group.parent).toBe(truckLayer);
  });

  test("a reliable truck without an error radius is a point only", () => {
    const { view, truckLayer } = makeView();
    view.setTrucks([ok(1, { geo_error_m: null })], NOW);
    const [dot] = of("circleMarker");
    expect(of("circle")).toEqual([]);
    expect(dot.parent).toBe(truckLayer);
  });

  test("an unreliable truck is a dashed circle with a popup", () => {
    const { view, truckLayer } = makeView();
    view.setTrucks([rough(2)], NOW);
    expect(of("circleMarker")).toEqual([]);
    const [area] = of("circle");
    expect(area.options).toMatchObject({ radius: 250, color: AGE_COLORS.recent, dashArray: "8 6" });
    expect(area.popup).not.toBeNull();
    expect(area.parent).toBe(truckLayer);
  });

  test("a new age colour is applied with setStyle, nothing is redrawn", () => {
    const { view } = makeView();
    view.setTrucks([ok(1), rough(2)], NOW);
    const drawn = h.layers.length;
    const [dot] = of("circleMarker");
    const [ring, area] = of("circle");

    view.setTrucks([ok(1), rough(2)], new Date(2026, 10, 6, 13, 0, 0));
    expect(h.layers).toHaveLength(drawn);
    expect(dot.styles).toEqual([{ fillColor: AGE_COLORS.today }]);
    expect(ring.styles).toEqual([{ color: AGE_COLORS.today, fillColor: AGE_COLORS.today }]);
    expect(area.styles).toEqual([{ color: AGE_COLORS.today, fillColor: AGE_COLORS.today }]);
    expect([dot, ring, area].map((l) => l.removed)).toEqual([0, 0, 0]);
  });

  test("a moved truck or a new radius is redrawn; a dropped truck is removed", () => {
    const { view } = makeView();
    view.setTrucks([ok(1), rough(2)], NOW);
    const firstGroup = of("group").find((g) => g.children.length === 2)!;
    const [area] = of("circle").filter((c) => c.options.dashArray);

    view.setTrucks([ok(1, { geo_error_m: 60 })], NOW);
    expect(firstGroup.removed).toBe(1);
    expect(area.removed).toBe(1);
    const rings = of("circle").filter((c) => !c.options.dashArray);
    expect(rings.map((r) => r.options.radius)).toEqual([42, 60]);

    view.setTrucks([ok(1, { geo_error_m: 60, lat: 12.5 })], NOW);
    expect(of("circleMarker").at(-1)!.at).toEqual([12.5, 77.01]);
    view.setTrucks([], NOW);
    const last = of("group").at(-1)!;
    expect(last.removed).toBe(1);
  });
});

describe("tiles", () => {
  test("failed and loaded tiles are reported", () => {
    makeView();
    const [tiles] = of("tiles");
    tiles.fire("tileerror");
    expect(onTileError).toHaveBeenCalledTimes(1);
    tiles.fire("tileload");
    expect(onTileLoad).toHaveBeenCalledTimes(1);
  });

  test("OpenStreetMap tiles credit OpenStreetMap with a link to its copyright page", () => {
    makeView();
    const [tiles] = of("tiles");
    expect(tiles.at).toBe(DEFAULT_TILE_URL);
    expect(tiles.options.attribution).toBe(
      `<a href="${ATTRIBUTION_URL}" target="_blank" rel="noopener noreferrer">© OpenStreetMap contributors</a>`,
    );
  });

  test("other tiles get the configured attribution as escaped text, without the link", () => {
    makeView("https://tiles.example/{z}/{x}/{y}.png", `Tiles <img src=x onerror="a()"> & co`);
    const [tiles] = of("tiles");
    expect(tiles.options.attribution).toBe(
      "Tiles &lt;img src=x onerror=&quot;a()&quot;&gt; &amp; co",
    );
  });
});

describe("popup", () => {
  test("shows the crop through mediaUrl, local time, score, error and a link to the truck", () => {
    const first = new Date(2026, 10, 3, 14, 5, 9);
    const truck = makeTruck(7, {
      first_seen: first.toISOString(),
      best_score: 0.87,
      crop_url: "/media/c7.jpg",
      geo_error_m: 6.5,
    });
    const el = popupContent(truck, (p) => `${p}?token=abc`);

    const img = el.querySelector("img")!;
    expect(img.getAttribute("src")).toBe("/media/c7.jpg?token=abc");
    expect(img.alt).toBe("Truck #7 close-up");
    expect(el.querySelector("p")!.textContent).toBe("3 Nov 14:05 · 87% · ±7 m");
    const link = el.querySelector("a")!;
    expect(link.getAttribute("href")).toBe("#/trucks/7");
    expect(link.textContent).toBe("Truck #7");
  });

  test("without a crop or an error radius there is no image and no ±", () => {
    const el = popupContent(makeTruck(8, { crop_url: null, geo_error_m: null }), (p) => p);
    expect(el.querySelector("img")).toBeNull();
    expect(el.querySelector("p")!.textContent).not.toContain("±");
    expect(el.querySelector("a")).not.toBeNull();
  });

  test("the drawn layers open the truck's popup", () => {
    const { view } = makeView();
    view.setTrucks([ok(1)], NOW);
    const [dot] = of("circleMarker");
    expect(dot.popup!().querySelector("a")!.getAttribute("href")).toBe("#/trucks/1");
    expect(dot.popup!().querySelector("p")!.textContent).toContain("±42 m");
  });
});
