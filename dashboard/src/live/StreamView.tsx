import { useEffect, useRef, useState } from "react";
import type { LiveState } from "../api/live";

const RETRY_MS = 2000;

function withBuster(src: string, n: number): string {
  if (n === 0) return src;
  return `${src}${src.includes("?") ? "&" : "?"}_=${n}`;
}

interface Props {
  src: string;
  /** When this goes from "reconnecting" back to "open" the engine restarted: reload the stream. */
  liveState?: LiveState;
}

export default function StreamView({ src, liveState }: Props) {
  const [buster, setBuster] = useState(0);
  const [failed, setFailed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevState = useRef(liveState);

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );

  useEffect(() => {
    if (prevState.current === "reconnecting" && liveState === "open") {
      setBuster((n) => n + 1);
    }
    prevState.current = liveState;
  }, [liveState]);

  function onError() {
    setFailed(true);
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      timer.current = null;
      setBuster((n) => n + 1);
    }, RETRY_MS);
  }

  function onLoad() {
    setFailed(false);
  }

  return (
    <div className="stream stream--bleed">
      <img
        src={withBuster(src, buster)}
        alt="Live drone video with truck boxes"
        onError={onError}
        onLoad={onLoad}
      />
      {failed && (
        <div className="stream-message" role="status">
          Video stream unavailable — retrying
        </div>
      )}
    </div>
  );
}
