// The only file that imports Leaflet. A small imperative wrapper the map page drives;
// the page's tests replace this whole module, so jsdom never runs Leaflet.
import * as L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { DronePosition, Truck } from "../api/types";
import { formatScore, formatTime } from "../format";
import { href } from "../router";
import { ATTRIBUTION_URL, DRONE_COLOR, planMarkers, type MarkerPlan } from "./mapModel";

export interface MapViewOptions {
  tileUrl: string;
  /** Attribution text; shown as a link to the OpenStreetMap copyright page. */
  attribution: string;
  /** Called for each tile that fails to load (the page decides what to say, and once). */
  onTileError: () => void;
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

/** A truck's popup: crop thumbnail, local time, score and a link to its page. Built with DOM calls, never HTML strings. */
function popupContent(truck: Truck, mediaUrl: (path: string) => string): HTMLElement {
  const root = document.createElement("div");
  root.className = "map-popup";
  if (truck.crop_url) {
    const img = document.createElement("img");
    img.src = mediaUrl(truck.crop_url);
    img.alt = `Truck #${truck.id} close-up`;
    root.append(img);
  }
  const meta = document.createElement("p");
  meta.textContent = `${formatTime(truck.first_seen)} · ${formatScore(truck.best_score)}`;
  const link = document.createElement("a");
  link.href = href({ page: "truck", id: truck.id });
  link.textContent = `Truck #${truck.id}`;
  root.append(meta, link);
  return root;
}

export function createMapView(el: HTMLElement, opts: MapViewOptions): MapView {
  const map = L.map(el, { worldCopyJump: true }).setView([20, 0], 2);
  map.attributionControl.setPrefix(false);

  const tiles = L.tileLayer(opts.tileUrl, {
    maxZoom: 19,
    attribution: `<a href="${ATTRIBUTION_URL}" target="_blank" rel="noopener noreferrer">${opts.attribution}</a>`,
  }).addTo(map);
  tiles.on("tileerror", () => opts.onTileError());

  const truckLayer = L.featureGroup().addTo(map);
  const droneLayer = L.featureGroup().addTo(map);
  const track = L.polyline([], { color: DRONE_COLOR, weight: 3, opacity: 0.85 }).addTo(droneLayer);
  let droneMarker: L.CircleMarker | null = null;

  function draw(plan: MarkerPlan): L.Path {
    const common = { color: OUTLINE, weight: 2 };
    if (plan.kind === "point") {
      return L.circleMarker([plan.lat, plan.lon], {
        ...common,
        radius: 8,
        fillColor: plan.color,
        fillOpacity: 0.95,
      });
    }
    // Where the truck may be: the dashed ring of its error radius, no centre point.
    return L.circle([plan.lat, plan.lon], {
      color: plan.color,
      weight: 3,
      dashArray: "8 6",
      radius: plan.radiusM,
      fillColor: plan.color,
      fillOpacity: 0.08,
    });
  }

  return {
    setTrucks(trucks, now) {
      truckLayer.clearLayers();
      for (const plan of planMarkers(trucks, now)) {
        draw(plan).bindPopup(() => popupContent(plan.truck, opts.mediaUrl)).addTo(truckLayer);
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
