// The only file that imports Leaflet. A small imperative wrapper the map page drives;
// the page's tests replace this whole module, so jsdom never runs Leaflet.
import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { DronePosition, Truck } from "../api/types";
import { formatScore, formatTime } from "../format";
import { href } from "../router";
import {
  DRONE_COLOR,
  attributionHtml,
  layerState,
  planMarkers,
  reconcileMarkers,
  type LayerState,
  type MarkerPlan,
} from "./mapModel";

export interface MapViewOptions {
  tileUrl: string;
  /** Plain-text credit for the tiles (escaped here; linked for OpenStreetMap's own tiles). */
  attribution: string;
  /** Called for each tile that fails to load (the page decides what to say, and once). */
  onTileError: () => void;
  /** Called for each tile that loads, so a notice about failed tiles can go away. */
  onTileLoad: () => void;
  /** Adds the access token to a media path, for the crop thumbnail in a popup. */
  mediaUrl: (path: string) => string;
}

export interface MapView {
  setTrucks(trucks: readonly Truck[], now: Date): void;
  setDrone(latest: DronePosition | null, track: readonly [number, number][]): void;
  fitToData(): void;
  destroy(): void;
}

const OUTLINE = "#1a1a1a";
/** Error circles of reliable points get a pane below the markers', so no circle covers a point. */
const ERROR_PANE = "truck-error";
const ERROR_PANE_Z = "350"; // Leaflet's overlay pane (markers and paths) is 400

/** A truck's popup: crop thumbnail, local time, score, error radius and a link to its page. Built with DOM calls, never HTML strings. */
export function popupContent(truck: Truck, mediaUrl: (path: string) => string): HTMLElement {
  const root = document.createElement("div");
  root.className = "map-popup";
  if (truck.crop_url) {
    const img = document.createElement("img");
    img.src = mediaUrl(truck.crop_url);
    img.alt = `Truck #${truck.id} close-up`;
    root.append(img);
  }
  const meta = document.createElement("p");
  const error = truck.geo_error_m === null ? "" : ` · ±${Math.round(truck.geo_error_m)} m`;
  meta.textContent = `${formatTime(truck.first_seen)} · ${formatScore(truck.best_score)}${error}`;
  const link = document.createElement("a");
  link.href = href({ page: "truck", id: truck.id });
  link.textContent = `Truck #${truck.id}`;
  root.append(meta, link);
  return root;
}

interface Drawn {
  layer: L.Layer;
  /** Applies a new age colour in place. */
  recolor: (color: string) => void;
  state: LayerState;
}

export function createMapView(el: HTMLElement, opts: MapViewOptions): MapView {
  const map = L.map(el, { worldCopyJump: true }).setView([20, 0], 2);
  map.attributionControl.setPrefix(false);
  map.createPane(ERROR_PANE).style.zIndex = ERROR_PANE_Z;

  const tiles = L.tileLayer(opts.tileUrl, {
    maxZoom: 19,
    attribution: attributionHtml(opts.attribution, opts.tileUrl),
  }).addTo(map);
  tiles.on("tileerror", () => opts.onTileError());
  tiles.on("tileload", () => opts.onTileLoad());

  const truckLayer = L.featureGroup().addTo(map);
  const droneLayer = L.featureGroup().addTo(map);
  const track = L.polyline([], { color: DRONE_COLOR, weight: 3, opacity: 0.85 }).addTo(droneLayer);
  let droneMarker: L.CircleMarker | null = null;

  const drawn = new Map<number, Drawn>();

  function draw(plan: MarkerPlan): Omit<Drawn, "state"> {
    const popup = () => popupContent(plan.truck, opts.mediaUrl);
    if (plan.kind === "point") {
      const dot = L.circleMarker([plan.lat, plan.lon], {
        color: OUTLINE,
        weight: 2,
        radius: 8,
        fillColor: plan.color,
        fillOpacity: 0.95,
      }).bindPopup(popup);
      if (plan.radiusM === null) {
        return { layer: dot, recolor: (color) => dot.setStyle({ fillColor: color }) };
      }
      // Its error circle: thin and lightly filled, under the point; clicks go through.
      const ring = L.circle([plan.lat, plan.lon], {
        pane: ERROR_PANE,
        interactive: false,
        radius: plan.radiusM,
        color: plan.color,
        weight: 1,
        opacity: 0.8,
        fillColor: plan.color,
        fillOpacity: 0.12,
      });
      return {
        layer: L.featureGroup([ring, dot]),
        recolor: (color) => {
          ring.setStyle({ color, fillColor: color });
          dot.setStyle({ fillColor: color });
        },
      };
    }
    // Where the truck may be: the dashed ring of its error radius, no centre point.
    const area = L.circle([plan.lat, plan.lon], {
      color: plan.color,
      weight: 3,
      dashArray: "8 6",
      radius: plan.radiusM,
      fillColor: plan.color,
      fillOpacity: 0.08,
    }).bindPopup(popup);
    return { layer: area, recolor: (color) => area.setStyle({ color, fillColor: color }) };
  }

  return {
    setTrucks(trucks, now) {
      const plans = planMarkers(trucks, now);
      const states = new Map([...drawn].map(([id, d]) => [id, d.state]));
      for (const change of reconcileMarkers(states, plans)) {
        if (change.action === "remove") {
          drawn.get(change.id)?.layer.remove();
          drawn.delete(change.id);
          continue;
        }
        const { plan } = change;
        const id = plan.truck.id;
        if (change.action === "recolor") {
          const entry = drawn.get(id)!;
          entry.recolor(plan.color);
          entry.state = layerState(plan);
          continue;
        }
        drawn.get(id)?.layer.remove();
        const shape = draw(plan);
        shape.layer.addTo(truckLayer);
        drawn.set(id, { ...shape, state: layerState(plan) });
      }
    },

    setDrone(latest, points) {
      track.setLatLngs(points.map(([lat, lon]) => L.latLng(lat, lon)));
      if (!latest) {
        droneMarker?.remove();
        droneMarker = null;
        return;
      }
      if (droneMarker) {
        droneMarker.setLatLng([latest.lat, latest.lon]);
      } else {
        droneMarker = L.circleMarker([latest.lat, latest.lon], {
          radius: 10,
          color: "#ffffff",
          weight: 3,
          fillColor: DRONE_COLOR,
          fillOpacity: 1,
        })
          .bindTooltip("Drone")
          .addTo(droneLayer);
      }
    },

    fitToData() {
      const bounds = L.latLngBounds([]);
      bounds.extend(truckLayer.getBounds());
      bounds.extend(droneLayer.getBounds());
      if (bounds.isValid()) map.fitBounds(bounds, { padding: [32, 32], maxZoom: 17 });
    },

    destroy() {
      map.remove();
    },
  };
}
