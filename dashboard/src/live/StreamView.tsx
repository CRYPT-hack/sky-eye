import { useEffect, useRef, useState } from "react";
import type { LiveState } from "../api/live";

const RETRY_MS = 2000;

function withBuster(src: string, n: number): string {
  if (n === 0) return src;
  return `${src}${src.includes("?") ? "&" : "?"}_=${n}`;
}

function isHidden(): boolean {
  return document.visibilityState === "hidden";
}

interface Props {
  src: string;
  /** When this goes from "reconnecting" back to "open" the engine restarted: reload the stream. */
  liveState?: LiveState;
}

/**
 * The live MJPEG stream. Chromium keeps a removed or hidden `<img>`'s multipart
 * download running, so the URL is dropped on unmount and while the tab is hidden
 * (`removeAttribute("src")` closes the connection without an error event; setting
 * `src = ""` would fire one), and put back with a fresh cache-buster when visible.
 */
export default function StreamView({ src, liveState }: Props) {
  const [buster, setBuster] = useState(0);
  const [failed, setFailed] = useState(false);
  const [hidden, setHidden] = useState(isHidden);
  const hiddenRef = useRef(hidden);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevState = useRef(liveState);
  const imgRef = useRef<HTMLImageElement>(null);
  const url = hidden ? undefined : withBuster(src, buster);
  const urlRef = useRef(url);
  urlRef.current = url;

  useEffect(() => {
    const img = imgRef.current;
    // StrictMode (dev) runs this cleanup and setup once more on the same element:
    // put back the URL the cleanup removed.
    if (img && urlRef.current !== undefined && img.getAttribute("src") !== urlRef.current) {
      img.setAttribute("src", urlRef.current);
    }
    return () => {
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = null;
      img?.removeAttribute("src");
    };
  }, []);

  useEffect(() => {
    const sync = () => {
      const nowHidden = isHidden();
      if (nowHidden === hiddenRef.current) return;
      hiddenRef.current = nowHidden;
      setHidden(nowHidden);
      if (!nowHidden) setBuster((n) => n + 1); // a fresh request, not a cached frame
    };
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);

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
        ref={imgRef}
        src={url}
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
