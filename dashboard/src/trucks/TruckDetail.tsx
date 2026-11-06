import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { Truck } from "../api/types";
import { useApi } from "../apiContext";
import Banner from "../components/Banner";
import { formatDuration, formatScore, formatTime } from "../format";
import { href } from "../router";

type State =
  | { kind: "loading" }
  | { kind: "ok"; truck: Truck }
  | { kind: "notfound" }
  | { kind: "error" };

export default function TruckDetail({ id }: { id: number }) {
  const { client, authEpoch, reportUnauthorized } = useApi();
  const [state, setState] = useState<State>({ kind: "loading" });
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    client
      .getTruck(id)
      .then((truck) => {
        if (!cancelled) setState({ kind: "ok", truck });
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) reportUnauthorized();
        else if (e instanceof ApiError && e.status === 404) setState({ kind: "notfound" });
        else setState({ kind: "error" });
      });
    return () => {
      cancelled = true;
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
