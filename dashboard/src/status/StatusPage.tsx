import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../api/client";
import type { Info, Status } from "../api/types";
import { useApi } from "../apiContext";
import Banner from "../components/Banner";
import { formatDuration, formatScore } from "../format";

const POLL_MS = 2000;
const SAVE_DEBOUNCE_MS = 400;
const SAVED_SHOWN_MS = 2000;

function isVisible(): boolean {
  return document.visibilityState === "visible";
}

function Fact({ label, value }: { label: string; value: string | number }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

export default function StatusPage() {
  const { client, token, reportUnauthorized } = useApi();
  const [info, setInfo] = useState<Info | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());

  const [confidence, setConfidence] = useState<number | null>(null);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const serverConfidence = useRef<number | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const saveSeq = useRef(0);
  const sliderId = useId();

  const fail = useCallback(
    (e: unknown): string | null => {
      if (e instanceof ApiError && e.status === 401) {
        reportUnauthorized();
        return null;
      }
      return e instanceof Error ? e.message : "Request failed";
    },
    [reportUnauthorized],
  );

  // Static facts and the current setting, loaded again when the token changes.
  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    Promise.all([client.getInfo(), client.getRuntime()])
      .then(([i, r]) => {
        if (cancelled) return;
        setInfo(i);
        serverConfidence.current = r.confidence;
        setConfidence(r.confidence);
      })
      .catch((e: unknown) => {
        if (!cancelled) setLoadError(fail(e));
      });
    return () => {
      cancelled = true;
    };
  }, [client, token, fail]);

  // Status polling and the uptime clock run only while the page is visible.
  useEffect(() => {
    let cancelled = false;
    let poll: ReturnType<typeof setInterval> | undefined;
    let clock: ReturnType<typeof setInterval> | undefined;

    const fetchStatus = () => {
      client
        .getStatus()
        .then((s) => {
          if (!cancelled) setStatus(s);
        })
        .catch((e: unknown) => {
          if (!cancelled) setLoadError(fail(e));
        });
    };
    const stop = () => {
      clearInterval(poll);
      clearInterval(clock);
      poll = clock = undefined;
    };
    const start = () => {
      if (poll !== undefined) return;
      fetchStatus();
      setNow(new Date());
      poll = setInterval(fetchStatus, POLL_MS);
      clock = setInterval(() => setNow(new Date()), 1000);
    };
    const sync = () => (isVisible() ? start() : stop());

    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", sync);
      stop();
    };
  }, [client, token, fail]);

  // A pending save must not fire after the page is gone.
  useEffect(
    () => () => {
      clearTimeout(saveTimer.current);
      clearTimeout(savedTimer.current);
      saveSeq.current += 1;
    },
    [],
  );

  function onSlide(value: number) {
    setConfidence(value);
    setSaved(false);
    setSaveError(null);
    clearTimeout(saveTimer.current);
    clearTimeout(savedTimer.current);
    saveTimer.current = setTimeout(() => {
      const seq = ++saveSeq.current;
      client
        .putRuntime({ confidence: value })
        .then((r) => {
          if (seq !== saveSeq.current) return;
          serverConfidence.current = r.confidence;
          setConfidence(r.confidence);
          setSaved(true);
          savedTimer.current = setTimeout(() => setSaved(false), SAVED_SHOWN_MS);
        })
        .catch((e: unknown) => {
          if (seq !== saveSeq.current) return;
          setConfidence(serverConfidence.current);
          setSaveError(fail(e));
        });
    }, SAVE_DEBOUNCE_MS);
  }

  const uptime = status ? formatDuration(status.started_at, now.toISOString()) : "–";

  return (
    <div className="stack">
      {loadError && <Banner tone="error">{loadError}</Banner>}

      <section className="section" aria-labelledby="status-engine">
        <h2 className="section-title" id="status-engine">
          Engine
        </h2>
        <dl className="facts">
          <Fact label="Version" value={info?.version ?? "–"} />
          <Fact
            label="Source"
            value={info ? `${info.source.type} · ${info.source.name} · ${info.source.fps} fps` : "–"}
          />
          <Fact label="Uptime" value={uptime} />
          <Fact label="Source state" value={status?.source_state ?? "–"} />
          <Fact label="Frames processed" value={status ? status.frames_processed : "–"} />
          <Fact label="FPS" value={status ? status.fps : "–"} />
        </dl>
      </section>

      <section className="section" aria-labelledby="status-detector">
        <h2 className="section-title" id="status-detector">
          Detector
        </h2>
        <dl className="facts">
          <Fact label="Model" value={info?.detector.model ?? "–"} />
          <Fact label="Input size" value={info ? `${info.detector.input_size} px` : "–"} />
          <Fact label="Device" value={info?.detector.device ?? "–"} />
          <Fact label="GPU" value={info ? (info.detector.gpu ?? "CPU") : "–"} />
        </dl>
      </section>

      <section className="section" aria-labelledby="status-settings">
        <h2 className="section-title" id="status-settings">
          Settings
        </h2>
        <div className="field">
          <div className="page-head">
            <label className="field-label" htmlFor={sliderId}>
              Confidence
            </label>
            <span>{confidence === null ? "–" : formatScore(confidence)}</span>
          </div>
          <input
            id={sliderId}
            type="range"
            min={0.05}
            max={0.95}
            step={0.05}
            value={confidence ?? 0.5}
            disabled={confidence === null}
            aria-valuetext={confidence === null ? undefined : formatScore(confidence)}
            onChange={(e) => onSlide(Number(e.target.value))}
          />
          <span className="field-hint" role="status">
            {saved ? "Saved" : ""}
          </span>
        </div>
        {saveError && <Banner tone="error">{saveError}</Banner>}
      </section>
    </div>
  );
}
