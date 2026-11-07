import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { connectLive, type LiveState } from "../api/live";
import type { DronePosition, Info, Status, Truck } from "../api/types";
import { useApi } from "../apiContext";
import Banner from "../components/Banner";
import { localDayRange } from "../trucks/dateRange";
import { createMapView, type MapView } from "./leafletView";
import {
  AGE_COLORS,
  AGE_LABELS,
  ATTRIBUTION_TEXT,
  DEFAULT_TILE_URL,
  appendTrack,
  isLocated,
  type AgeBucket,
} from "./mapModel";

const PAGE_SIZE = 200;
const MAX_TRUCKS = 1000;
/** How much drone track to ask the engine for when the page opens. */
const TRACK_SECONDS = 300;

/** Marker colours follow the clock: re-plan them this often. */
const RECOLOR_MS = 60_000;

const AGE_ORDER: AgeBucket[] = ["recent", "today", "older"];

interface Drone {
  latest: DronePosition | null;
  track: [number, number][];
}

/** Today as the value of a date input (local `YYYY-MM-DD`). */
function localToday(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** The two lists together by id; a truck in `newer` replaces the same truck in `older`. */
function mergeById(older: readonly Truck[], newer: readonly Truck[]): Truck[] {
  const byId = new Map<number, Truck>();
  for (const t of older) byId.set(t.id, t);
  for (const t of newer) byId.set(t.id, t);
  return [...byId.values()];
}

function inRange(truck: Truck, range: { from?: string; to?: string }): boolean {
  const seen = Date.parse(truck.first_seen);
  if (range.from !== undefined && seen < Date.parse(range.from)) return false;
  if (range.to !== undefined && seen >= Date.parse(range.to)) return false;
  return true;
}

function unlocatedNote(n: number): string {
  return n === 1
    ? "1 truck in this range has no reliable location"
    : `${n} trucks in this range have no reliable location`;
}

export default function MapPage({ connect = connectLive }: { connect?: typeof connectLive }) {
  const { client, authEpoch, reportUnauthorized } = useApi();
  const [from, setFrom] = useState(localToday);
  const [to, setTo] = useState(from);
  const [applied, setApplied] = useState({ from, to });

  const [info, setInfo] = useState<Info | null>(null);
  const [tileUrl, setTileUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [trucks, setTrucks] = useState<Truck[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [capped, setCapped] = useState(false);
  const [retry, setRetry] = useState(0);
  const [drone, setDrone] = useState<Drone>({ latest: null, track: [] });
  const [tilesFailed, setTilesFailed] = useState(false);
  const [view, setView] = useState<MapView | null>(null);
  /** Goes up whenever a fresh set of data arrives, so the map zooms to it once. */
  const [fitTick, setFitTick] = useState(0);
  /** Goes up every minute so "Last hour" markers turn into "Earlier today" on a quiet page. */
  const [clock, setClock] = useState(0);
  /** Goes up after each live reconnect: the trucks and track are fetched again. */
  const [gaps, setGaps] = useState(0);
  const quietReload = useRef(false);

  const mapEl = useRef<HTMLDivElement>(null);
  const range = localDayRange(applied.from, applied.to);
  const rangeRef = useRef(range);
  rangeRef.current = range;
  const reportRef = useRef(reportUnauthorized);
  reportRef.current = reportUnauthorized;

  // What the engine is: where the tiles come from and whether it has a drone position.
  useEffect(() => {
    let cancelled = false;
    const unauthorized = (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) reportRef.current();
    };
    client
      .getInfo()
      .then((i) => {
        if (cancelled) return;
        setInfo(i);
        setTileUrl(i.map_tile_url || DEFAULT_TILE_URL);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        unauthorized(e);
        setTileUrl((cur) => cur ?? DEFAULT_TILE_URL);
      });
    client
      .getStatus()
      .then((s) => {
        if (!cancelled) setStatus(s);
      })
      .catch(unauthorized);
    return () => {
      cancelled = true;
    };
  }, [client, authEpoch]);

  const telemetryMode = info === null ? null : (info.telemetry ?? "none");

  // The drone's recent track; after this the live connection keeps it current.
  useEffect(() => {
    if (telemetryMode === null || telemetryMode === "none") return;
    let cancelled = false;
    client
      .getTelemetry(TRACK_SECONDS)
      .then((t) => {
        if (cancelled) return;
        setDrone({ latest: t.latest, track: t.track });
        if (gaps === 0) setFitTick((n) => n + 1);
      })
      .catch((e: unknown) => {
        if (!cancelled && e instanceof ApiError && e.status === 401) reportRef.current();
      });
    return () => {
      cancelled = true;
    };
  }, [client, authEpoch, telemetryMode, gaps]);

  // Every truck in the range, a page at a time, up to MAX_TRUCKS.
  useEffect(() => {
    let cancelled = false;
    // After a reconnect the rows on screen stay while the missed ones are fetched.
    const quiet = quietReload.current;
    quietReload.current = false;
    if (!quiet) {
      setLoading(true);
      setFailed(false);
      setCapped(false);
      setTrucks([]);
    }
    const wanted = localDayRange(applied.from, applied.to);
    void (async () => {
      try {
        const loaded: Truck[] = [];
        let offset = 0;
        let hitCap = false;
        for (;;) {
          const page = await client.listTrucks({ ...wanted, limit: PAGE_SIZE, offset });
          if (cancelled) return;
          loaded.push(...page.trucks);
          offset += page.trucks.length;
          if (page.trucks.length < PAGE_SIZE) break;
          if (offset >= MAX_TRUCKS) {
            hitCap = true;
            break;
          }
        }
        // Trucks that arrived live while the pages were loading are newer: they win.
        setTrucks((live) => mergeById(loaded, live));
        setCapped(hitCap);
        if (!quiet) setFitTick((n) => n + 1);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) reportRef.current();
        else if (!quiet) setFailed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [client, authEpoch, applied, retry]);

  // One live connection: trucks in the range, drone positions and the status.
  useEffect(() => {
    let prev: LiveState = "connecting";
    return connect({
      url: () => client.liveUrl(),
      onMessage: (m) => {
        if (m.type === "status") {
          setStatus(m.status);
        } else if (m.type === "telemetry") {
          const latest = m.latest;
          if (latest) setDrone((d) => ({ latest, track: appendTrack(d.track, latest) }));
        } else if (inRange(m.truck, rangeRef.current)) {
          setTrucks((cur) => mergeById(cur, [m.truck]));
        }
      },
      onState: (s) => {
        if (s === "unauthorized") reportRef.current();
        // Back after a gap: fetch what was missed (the page's trucks and the drone track).
        if (prev === "reconnecting" && s === "open") {
          quietReload.current = true;
          setRetry((n) => n + 1);
          setGaps((n) => n + 1);
        }
        prev = s;
      },
    });
  }, [client, authEpoch, connect]);

  // The map itself, once the tile source is known.
  useEffect(() => {
    const el = mapEl.current;
    if (!el || tileUrl === null) return;
    const v = createMapView(el, {
      tileUrl,
      attribution: ATTRIBUTION_TEXT,
      mediaUrl: client.mediaUrl,
      onTileError: () => setTilesFailed(true),
    });
    setView(v);
    return () => {
      v.destroy();
      setView(null);
    };
  }, [client, tileUrl]);

  useEffect(() => {
    const id = setInterval(() => setClock((n) => n + 1), RECOLOR_MS);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    view?.setTrucks(trucks, new Date());
  }, [view, trucks, clock]);
  useEffect(() => {
    view?.setDrone(drone.latest, drone.track);
  }, [view, drone]);
  // After the two above, so the fit sees the data they just drew.
  useEffect(() => {
    if (view && fitTick > 0) view.fitToData();
  }, [view, fitTick]);

  const located = trucks.filter(isLocated).length;
  const unlocated = trucks.length - located;

  return (
    <>
      <form
        className="map-filter"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied({ from, to });
        }}
      >
        <div className="field">
          <label className="field-label" htmlFor="map-from">
            From
          </label>
          <input
            id="map-from"
            className="input"
            type="date"
            value={from}
            max={to || undefined}
            onChange={(e) => setFrom(e.target.value)}
          />
        </div>
        <div className="field">
          <label className="field-label" htmlFor="map-to">
            To
          </label>
          <input
            id="map-to"
            className="input"
            type="date"
            value={to}
            min={from || undefined}
            onChange={(e) => setTo(e.target.value)}
          />
        </div>
        <button type="submit" className="button button--primary">
          Apply
        </button>
        <button type="button" className="button" onClick={() => view?.fitToData()}>
          Fit to data
        </button>
      </form>

      {failed && (
        <Banner tone="error">
          <span>Could not load trucks.</span>{" "}
          <button type="button" className="button" onClick={() => setRetry((n) => n + 1)}>
            Retry
          </button>
        </Banner>
      )}
      {telemetryMode === "none" && (
        <Banner tone="info">
          No drone position data — set <code>telemetry</code> in config.yaml (see README: GPS &amp;
          map)
        </Banner>
      )}
      {telemetryMode !== null && telemetryMode !== "none" && status?.telemetry_state === "stale" && (
        <Banner tone="warn">Drone position data is stale</Banner>
      )}
      {telemetryMode !== null && telemetryMode !== "none" && status?.telemetry_state === "waiting" && (
        <Banner tone="info">Waiting for drone position…</Banner>
      )}
      {tilesFailed && <Banner tone="warn">Map tiles unavailable — showing points only</Banner>}
      {capped && <Banner tone="info">Showing the first {MAX_TRUCKS} trucks in this range</Banner>}
      {!loading && !failed && located === 0 && (
        <Banner tone="info">No trucks with a location in this range</Banner>
      )}

      <div ref={mapEl} className="map-canvas" role="region" aria-label="Map" />

      <ul className="map-legend" aria-label="Marker colours by age">
        {AGE_ORDER.map((bucket) => (
          <li key={bucket}>
            <span
              className="map-swatch"
              style={{ backgroundColor: AGE_COLORS[bucket] }}
              aria-hidden="true"
            />
            {AGE_LABELS[bucket]}
          </li>
        ))}
      </ul>
      <p className="field-hint">
        Dashed circles are approximate locations (the ring is the error). The green dot is the
        drone and its recent track.
      </p>
      {unlocated > 0 && <p className="muted">{unlocatedNote(unlocated)}</p>}
    </>
  );
}
