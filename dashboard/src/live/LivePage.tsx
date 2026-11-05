import { useCallback, useRef, useState } from "react";
import { useApi } from "../apiContext";
import type { connectLive } from "../api/live";
import type { Truck } from "../api/types";
import Banner from "../components/Banner";
import TruckCard from "../components/TruckCard";
import { beep, loadSoundEnabled, saveSoundEnabled } from "./sound";
import StreamView from "./StreamView";
import { useLive } from "./useLive";

const SOURCE_LABEL = {
  starting: "Starting",
  running: "Running",
  ended: "Ended",
  error: "Error",
} as const;

export default function LivePage({ connect }: { connect?: typeof connectLive }) {
  const { client } = useApi();
  const [soundOn, setSoundOn] = useState(loadSoundEnabled);
  const soundRef = useRef(soundOn);
  soundRef.current = soundOn;
  const [newIds, setNewIds] = useState<ReadonlySet<number>>(new Set());

  const onNewTruck = useCallback((truck: Truck) => {
    setNewIds((s) => new Set(s).add(truck.id));
    if (soundRef.current) beep();
  }, []);

  const { status, trucks, state } = useLive(client, { connect, onNewTruck });

  function toggleSound() {
    const next = !soundOn;
    setSoundOn(next);
    saveSoundEnabled(next);
  }

  return (
    <>
      <StreamView src={client.streamUrl()} liveState={state} />

      {state === "reconnecting" && <Banner tone="warn">Reconnecting to the engine…</Banner>}
      {status?.source_state === "ended" && (
        <Banner tone="info">Video finished — trucks stay browsable</Banner>
      )}
      {status?.source_state === "error" && (
        <Banner tone="error">Video source error — check the engine log</Banner>
      )}

      <div className="stats">
        <div className="stat stat--truck">
          <div className="stat-label">Trucks now</div>
          <div className="stat-value">{status ? status.trucks_now : "–"}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Total this run</div>
          <div className="stat-value">{status ? status.trucks_total : "–"}</div>
        </div>
        <div className="stat">
          <div className="stat-label">FPS</div>
          <div className="stat-value">{status ? status.fps.toFixed(1) : "–"}</div>
        </div>
        <div className="stat">
          <div className="stat-label">Source</div>
          <div className="stat-value stat-value--text">
            {status ? SOURCE_LABEL[status.source_state] : "–"}
          </div>
        </div>
      </div>

      <section className="section" aria-labelledby="latest-trucks">
        <div className="page-head">
          <h2 className="section-title" id="latest-trucks">
            Latest trucks
          </h2>
          <button
            type="button"
            className="button button--quiet"
            aria-pressed={soundOn}
            onClick={toggleSound}
          >
            Sound alert
          </button>
        </div>
        {trucks.length === 0 ? (
          <div className="empty">
            <p>No trucks yet. They show up here as the drone video is analysed.</p>
          </div>
        ) : (
          <div className="strip">
            {trucks.map((t) => (
              <TruckCard
                key={t.id}
                truck={t}
                mediaUrl={client.mediaUrl}
                compact
                isNew={newIds.has(t.id)}
              />
            ))}
          </div>
        )}
      </section>
    </>
  );
}
