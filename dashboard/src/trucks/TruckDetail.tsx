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

/** No clip yet, but last seen under CLIP_WAIT_MS ago: the engine may still be saving it. */
function clipPending(truck: Truck, now: number): boolean {
  if (truck.clip_url !== null) return false;
  const lastSeen = Date.parse(truck.last_seen);
  return Number.isFinite(lastSeen) && now - lastSeen < CLIP_WAIT_MS;
}

type State =
  | { kind: "loading" }
  | { kind: "ok"; truck: Truck; clipPending: boolean }
  | { kind: "notfound" }
  | { kind: "error" };

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
    setState({ kind: "loading" });

    const load = () => {
      client
        .getTruck(id)
        .then((truck) => {
          if (cancelled) return;
          shown = truck;
          const pending = clipPending(truck, Date.now());
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
            if (clipPending(shown, Date.now())) timer = setTimeout(load, CLIP_POLL_MS);
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
      {t.clip_url ? (
        <video
          className="detail-media"
          controls
          playsInline
          preload="metadata"
          src={client.mediaUrl(t.clip_url)}
        />
      ) : state.clipPending ? (
        <p className="muted" role="status">
          Clip is being saved…
        </p>
      ) : (
        <p className="muted">No clip saved for this truck</p>
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
          {t.geo_status === "unknown" || t.lat === null || t.lon === null
            ? "Unknown"
            : `${t.lat.toFixed(5)}, ${t.lon.toFixed(5)}`}
        </dd>
      </dl>
    </div>
  );
}
