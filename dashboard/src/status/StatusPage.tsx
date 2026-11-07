import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ApiError } from "../api/client";
import type { Info, Status } from "../api/types";
import { useApi } from "../apiContext";
import Banner from "../components/Banner";
import {
  formatAlertChannel,
  formatDuration,
  formatFps,
  formatScore,
  formatSourceState,
  formatTelemetryState,
} from "../format";

const POLL_MS = 2000;
const SAVE_DEBOUNCE_MS = 400;
const SAVED_SHOWN_MS = 2000;

const UNREACHABLE = "Cannot reach the engine";

/** Source frame rate as the engine reports it, to 2 decimals at most: `29.97`, `30`. */
function sourceFps(fps: number): string {
  return String(Math.round(fps * 100) / 100);
}

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
  const { client, authEpoch, reportUnauthorized } = useApi();
  const [info, setInfo] = useState<Info | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [infoError, setInfoError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());

  const [confidence, setConfidence] = useState<number | null>(null);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const serverConfidence = useRef<number | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const savedTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const saveSeq = useRef(0);
  const sliderId = useId();

  const [alerts, setAlerts] = useState<Record<string, boolean>>({});
  const [alertError, setAlertError] = useState<string | null>(null);
  const serverAlerts = useRef<Record<string, boolean>>({});
  const alertSeq = useRef<Record<string, number>>({});
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);
  const testSeq = useRef(0);

  const fail = useCallback(
    (e: unknown): string | null => {
      if (e instanceof ApiError && e.status === 401) {
        reportUnauthorized();
        return null;
      }
      // fetch() rejects with a TypeError when the engine is down or unreachable.
      if (e instanceof TypeError) return UNREACHABLE;
      return e instanceof Error ? e.message : "Request failed";
    },
    [reportUnauthorized],
  );

  // Static facts, the current setting and the status poll. Status polling and the uptime
  // clock run only while the page is visible; a failed info/runtime load is retried on
  // each poll tick until it succeeds.
  useEffect(() => {
    let cancelled = false;
    let loaded = false;
    let loading = false;
    let poll: ReturnType<typeof setInterval> | undefined;
    let clock: ReturnType<typeof setInterval> | undefined;

    const load = () => {
      if (loaded || loading) return;
      loading = true;
      Promise.all([client.getInfo(), client.getRuntime()])
        .then(([i, r]) => {
          loaded = true;
          if (cancelled) return;
          setInfo(i);
          serverConfidence.current = r.confidence;
          setConfidence(r.confidence);
          serverAlerts.current = { ...r.alerts };
          setAlerts({ ...r.alerts });
          setInfoError(null);
        })
        .catch((e: unknown) => {
          if (!cancelled) setInfoError(fail(e));
        })
        .finally(() => {
          loading = false;
        });
    };
    const fetchStatus = () => {
      load();
      client
        .getStatus()
        .then((s) => {
          if (cancelled) return;
          setStatus(s);
          setPollError(null);
        })
        .catch((e: unknown) => {
          if (!cancelled) setPollError(fail(e));
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

    setInfoError(null);
    load();
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", sync);
      stop();
    };
  }, [client, authEpoch, fail]);

  // A pending save must not fire after the page is gone.
  useEffect(
    () => () => {
      clearTimeout(saveTimer.current);
      clearTimeout(savedTimer.current);
      saveSeq.current += 1;
      testSeq.current += 1;
      for (const k of Object.keys(alertSeq.current)) alertSeq.current[k] += 1;
    },
    [],
  );

  // Each channel is saved on its own, by name only, so switches never overwrite each other;
  // a response only counts for the latest toggle of its own channel.
  function onToggle(name: string, value: boolean) {
    const seq = (alertSeq.current[name] = (alertSeq.current[name] ?? 0) + 1);
    setAlerts((a) => ({ ...a, [name]: value }));
    setAlertError(null);
    client
      .putRuntime({ alerts: { [name]: value } })
      .then((r) => {
        const confirmed = r.alerts?.[name] ?? value;
        serverAlerts.current[name] = confirmed;
        if (seq !== alertSeq.current[name]) return;
        setAlerts((a) => ({ ...a, [name]: confirmed }));
      })
      .catch((e: unknown) => {
        if (seq !== alertSeq.current[name]) return;
        setAlerts((a) => ({ ...a, [name]: serverAlerts.current[name] ?? !value }));
        setAlertError(fail(e));
      });
  }

  function onTestAlert() {
    const seq = ++testSeq.current;
    setTesting(true);
    setTestResult(null);
    client
      .sendTestAlert()
      .then((sent) => {
        if (seq !== testSeq.current) return;
        setTestResult({
          ok: true,
          text:
            sent.length > 0
              ? `Test queued for: ${sent.map(formatAlertChannel).join(", ")} — check the engine log if nothing arrives.`
              : "No channel is switched on",
        });
      })
      .catch((e: unknown) => {
        if (seq !== testSeq.current) return;
        const text = fail(e);
        if (text !== null) setTestResult({ ok: false, text });
      })
      .finally(() => {
        if (seq === testSeq.current) setTesting(false);
      });
  }

  function onSlide(value: number) {
    // Any response to an earlier choice is now stale.
    saveSeq.current += 1;
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
          serverConfidence.current = r.confidence;
          if (seq !== saveSeq.current) return;
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
  // One banner for both requests: when the engine is down both fail the same way.
  const loadErrors = [...new Set([infoError, pollError].filter((m): m is string => !!m))];

  return (
    <div className="stack">
      {loadErrors.length > 0 && <Banner tone="error">{loadErrors.join(" · ")}</Banner>}

      <section className="section" aria-labelledby="status-engine">
        <h2 className="section-title" id="status-engine">
          Engine
        </h2>
        <dl className="facts">
          <Fact label="Version" value={info?.version ?? "–"} />
          <Fact
            label="Source"
            value={
              info
                ? `${info.source.type} · ${info.source.name} · ${sourceFps(info.source.fps)} fps`
                : "–"
            }
          />
          <Fact label="Uptime" value={uptime} />
          <Fact
            label="Source state"
            value={status ? formatSourceState(status.source_state) : "–"}
          />
          <Fact label="Frames processed" value={status ? status.frames_processed : "–"} />
          <Fact label="FPS" value={status ? formatFps(status.fps) : "–"} />
          <Fact
            label="Drone position"
            value={status ? formatTelemetryState(status.telemetry_state) : "–"}
          />
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

      {info?.alerts && info.alerts.length > 0 && (
        <section className="section" aria-labelledby="status-alerts">
          <h2 className="section-title" id="status-alerts">
            Alerts
          </h2>
          <div className="stack">
            {info.alerts.map((name) => (
              <label key={name} className="switch">
                <input
                  type="checkbox"
                  role="switch"
                  checked={alerts[name] ?? false}
                  onChange={(e) => onToggle(name, e.target.checked)}
                />
                <span>{formatAlertChannel(name)}</span>
              </label>
            ))}
            {alertError && <Banner tone="error">{alertError}</Banner>}
            <div>
              <button type="button" className="button" disabled={testing} onClick={onTestAlert}>
                Send test alert
              </button>
            </div>
            {testResult &&
              (testResult.ok ? (
                <p className="muted" role="status">
                  {testResult.text}
                </p>
              ) : (
                <Banner tone="error">{testResult.text}</Banner>
              ))}
          </div>
        </section>
      )}
    </div>
  );
}
