import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { Truck } from "../api/types";
import { useApi } from "../apiContext";
import Banner from "../components/Banner";
import { formatDuration, formatScore, formatTime } from "../format";
import { href } from "../router";

/** A truck seen this recently may still have its clip encoding (up to about a minute). */
const CLIP_WAIT_MS = 120_000;
const CLIP_POLL_MS = 3000;
/** With clip_status the engine always resolves "pending"; stop asking after this, just in case. */
const CLIP_PENDING_MAX_MS = 600_000;

const CLIP_MESSAGE = {
  skipped: "No clip: too many trucks at once",
  failed: "Clip could not be saved",
  removed: "Clip removed to free disk space",
} as const;

/** No clip yet, but last seen under CLIP_WAIT_MS ago: the engine may still be saving it. */
function clipPending(truck: Truck, now: number): boolean {
  if (truck.clip_url !== null) return false;
  const lastSeen = Date.parse(truck.last_seen);
  return Number.isFinite(lastSeen) && now - lastSeen < CLIP_WAIT_MS;
}

/**
 * Whether to fetch the truck again for its clip. Engines that report clip_status say so
 * themselves (until `since`+10 min); older ones get the last_seen heuristic.
 */
function keepPolling(truck: Truck, now: number, since: number): boolean {
  if (truck.clip_status === undefined) return clipPending(truck, now);
  return truck.clip_status === "pending" && now - since < CLIP_PENDING_MAX_MS;
}

type State =
  | { kind: "loading" }
  | { kind: "ok"; truck: Truck; clipPending: boolean }
  | { kind: "notfound" }
  | { kind: "error" };

/** What to show in place of the player when the truck has no clip file. */
function clipNotice(t: Truck, polling: boolean) {
  const status = t.clip_status;
  if (status === undefined) {
    return polling ? (
      <p className="muted" role="status">
        Clip is being saved…
      </p>
    ) : (
      <p className="muted">No clip saved for this truck</p>
    );
  }
  if (status === "pending") {
    return polling ? (
      <p className="muted" role="status">
        Clip is being saved…
      </p>
    ) : (
      <p className="muted">Clip status unknown</p>
    );
  }
  // "saved" without a clip_url should not happen; skipped, failed and removed explain themselves.
  if (status === "saved") return <p className="muted">No clip saved for this truck</p>;
  if (Object.hasOwn(CLIP_MESSAGE, status)) {
    return <p className="muted">{CLIP_MESSAGE[status as keyof typeof CLIP_MESSAGE]}</p>;
  }
  // A status from a newer engine that this site does not know yet.
  return <p className="muted">{`Clip status: ${String(status)}`}</p>;
}

/** The player only for a saved clip; engines without clip_status: whenever there is a clip. */
function playableClip(t: Truck): string | null {
  if (t.clip_status !== undefined && t.clip_status !== "saved") return null;
  return t.clip_url;
}

/** The Location row: coordinates with their error, a map link when the fix is reliable. */
function LocationValue({ truck: t }: { truck: Truck }) {
  const { lat: latitude, lon: longitude } = t;
  const error = t.geo_error_m === null ? "" : ` (±${Math.round(t.geo_error_m)} m)`;
  if (t.geo_status === "ok" && latitude !== null && longitude !== null) {
    const lat = latitude.toFixed(5);
    const lon = longitude.toFixed(5);
    return (
      <>
        {`${lat}, ${lon}${error}`}{" "}
        <a
          href={`https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=17/${lat}/${lon}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          Open in OpenStreetMap
        </a>
      </>
    );
  }
  if (t.geo_status === "unreliable") {
    return latitude !== null && longitude !== null
      ? `Approximate: ${latitude.toFixed(5)}, ${longitude.toFixed(5)}${error}`
      : "Not reliable (camera angle or height unknown or too shallow)";
  }
  return "Unknown";
}

export default function TruckDetail({ id }: { id: number }) {
  const { client, authEpoch, reportUnauthorized } = useApi();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [retry, setRetry] = useState(0);

  // Loads the truck; while its clip may still be saving, fetches it again every
  // CLIP_POLL_MS (which also refreshes last_seen and the duration).
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let shown: Truck | null = null;
    const since = Date.now();
    setState({ kind: "loading" });

    const load = () => {
      client
        .getTruck(id)
        .then((truck) => {
          if (cancelled) return;
          shown = truck;
          const pending = keepPolling(truck, Date.now(), since);
          setState({ kind: "ok", truck, clipPending: pending });
          if (pending) timer = setTimeout(load, CLIP_POLL_MS);
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          if (e instanceof ApiError && e.status === 401) {
            reportUnauthorized(); // a saved token starts a new auth epoch: this effect runs again
            return;
          }
          if (shown !== null) {
            // A failed re-check keeps the truck on screen and tries again.
            if (keepPolling(shown, Date.now(), since)) timer = setTimeout(load, CLIP_POLL_MS);
            else setState({ kind: "ok", truck: shown, clipPending: false });
            return;
          }
          if (e instanceof ApiError && e.status === 404) setState({ kind: "notfound" });
          else setState({ kind: "error" });
        });
    };
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [client, id, authEpoch, retry, reportUnauthorized]);

  const back = (
    <div>
      <a className="button button--quiet" href={href({ page: "trucks" })}>
        Back to trucks
      </a>
    </div>
  );

  if (state.kind === "notfound") {
    return (
      <div className="stack">
        <div className="empty">
          <p>Truck not found</p>
        </div>
        {back}
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="stack">
        <Banner tone="error">
          <span>Could not load this truck.</span>{" "}
          <button type="button" className="button" onClick={() => setRetry((n) => n + 1)}>
            Retry
          </button>
        </Banner>
        {back}
      </div>
    );
  }
  if (state.kind === "loading") {
    return (
      <div className="stack">
        <p className="muted">Loading...</p>
        {back}
      </div>
    );
  }

  const t = state.truck;
  const clip = playableClip(t);
  return (
    <div className="stack">
      {back}
      {t.crop_url && (
        <img
          className="detail-media detail-crop"
          src={client.mediaUrl(t.crop_url)}
          alt={`Truck #${t.id} close-up`}
        />
      )}
      {t.snapshot_url ? (
        <img
          className="detail-media"
          src={client.mediaUrl(t.snapshot_url)}
          alt={`Truck #${t.id} full frame`}
        />
      ) : (
        <div className="empty">
          <p>No full frame saved</p>
        </div>
      )}
      {clip ? (
        <video
          className="detail-media"
          controls
          playsInline
          preload="metadata"
          src={client.mediaUrl(clip)}
        />
      ) : (
        clipNotice(t, state.clipPending)
      )}
      <dl className="facts">
        <dt>First seen</dt>
        <dd>{formatTime(t.first_seen)}</dd>
        <dt>Last seen</dt>
        <dd>{formatTime(t.last_seen)}</dd>
        <dt>On screen for</dt>
        <dd>{formatDuration(t.first_seen, t.last_seen)}</dd>
        <dt>Best score</dt>
        <dd>{formatScore(t.best_score)}</dd>
        <dt>Run</dt>
        <dd>{t.run_id}</dd>
        <dt>Track</dt>
        <dd>{t.track_id}</dd>
        <dt>Location</dt>
        <dd>
          <LocationValue truck={t} />
        </dd>
      </dl>
    </div>
  );
}
