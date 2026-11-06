import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import type { Truck } from "../api/types";
import { useApi } from "../apiContext";
import Banner from "../components/Banner";
import TruckCard from "../components/TruckCard";
import { localDayRange } from "./dateRange";
import { clearGallery, loadGallery, saveGallery, type DayRange } from "./galleryStore";

const PAGE_SIZE = 48;

/** What the rows on screen were loaded for: the first page is fetched again only when this changes. */
interface LoadedFor {
  applied: DayRange;
  authEpoch: number;
  retry: number;
}

export default function TrucksPage() {
  const { client, authEpoch, unauthorized, reportUnauthorized } = useApi();
  // Coming back from a truck's detail page: show the gallery as it was left.
  const [restored] = useState(() => loadGallery(client, authEpoch));
  const [from, setFrom] = useState(restored?.applied.from ?? "");
  const [to, setTo] = useState(restored?.applied.to ?? "");
  const [applied, setApplied] = useState<DayRange>(() => restored?.applied ?? { from: "", to: "" });
  const [trucks, setTrucks] = useState<Truck[]>(restored?.trucks ?? []);
  const [hasMore, setHasMore] = useState(restored?.hasMore ?? false);
  const [loading, setLoading] = useState(restored === null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const requestId = useRef(0);
  const offsetRef = useRef(restored?.offset ?? 0);
  const loadedFor = useRef<LoadedFor | null>(
    restored ? { applied, authEpoch, retry } : null,
  );

  // Read by the unmount cleanup, which only sees the first render's values.
  const latest = useRef({ client, authEpoch, applied, trucks, hasMore });
  latest.current = { client, authEpoch, applied, trucks, hasMore };

  const fetchPage = useCallback(
    async (offset: number, range: DayRange, append: boolean, key?: LoadedFor) => {
      const id = ++requestId.current;
      setLoading(true);
      setFailed(false);
      try {
        const res = await client.listTrucks({
          limit: PAGE_SIZE,
          offset,
          ...localDayRange(range.from, range.to),
        });
        if (id !== requestId.current) return;
        offsetRef.current = offset + res.trucks.length;
        if (key) loadedFor.current = key;
        setTrucks((prev) => {
          if (!append) return res.trucks;
          // Trucks saved since the last page shift the offset: skip rows already shown.
          const seen = new Set(prev.map((t) => t.id));
          return [...prev, ...res.trucks.filter((t) => !seen.has(t.id))];
        });
        setHasMore(res.trucks.length >= PAGE_SIZE);
      } catch (e) {
        if (id !== requestId.current) return;
        if (e instanceof ApiError && e.status === 401) reportUnauthorized();
        else setFailed(true);
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    },
    [client, reportUnauthorized],
  );

  useEffect(() => {
    const done = loadedFor.current;
    if (done && done.applied === applied && done.authEpoch === authEpoch && done.retry === retry) {
      return; // restored, or already loaded (a StrictMode replay)
    }
    void fetchPage(0, applied, false, { applied, authEpoch, retry });
    return () => {
      requestId.current++;
    };
  }, [fetchPage, applied, authEpoch, retry]);

  // A layout effect: its cleanup runs before the gallery leaves the DOM, while
  // window.scrollY is still the gallery's scroll position.
  useLayoutEffect(() => {
    if (restored) window.scrollTo(0, restored.scrollY);
    return () => {
      requestId.current++; // drop a "Load more" still in flight
      const cur = latest.current;
      const done = loadedFor.current;
      if (done && done.applied === cur.applied && done.authEpoch === cur.authEpoch) {
        saveGallery(cur.client, cur.authEpoch, {
          applied: cur.applied,
          trucks: cur.trucks,
          offset: offsetRef.current,
          hasMore: cur.hasMore,
          scrollY: window.scrollY,
        });
      } else {
        clearGallery(); // left before the rows matched the filter: load afresh next time
      }
    };
    // Mount and unmount only; everything else is read through refs.
  }, []);

  const clear = () => {
    clearGallery();
    loadedFor.current = null;
    setFrom("");
    setTo("");
    setApplied({ from: "", to: "" });
  };

  return (
    <>
      <form
        className="section"
        onSubmit={(e) => {
          e.preventDefault();
          setApplied({ from, to });
        }}
      >
        <div className="stack">
          <div className="field">
            <label className="field-label" htmlFor="trucks-from">
              From
            </label>
            <input
              id="trucks-from"
              className="input"
              type="date"
              value={from}
              max={to || undefined}
              onChange={(e) => setFrom(e.target.value)}
            />
          </div>
          <div className="field">
            <label className="field-label" htmlFor="trucks-to">
              To
            </label>
            <input
              id="trucks-to"
              className="input"
              type="date"
              value={to}
              min={from || undefined}
              onChange={(e) => setTo(e.target.value)}
            />
          </div>
          <div className="page-head">
            <button type="submit" className="button button--primary">
              Apply
            </button>
            <button type="button" className="button button--quiet" onClick={clear}>
              Clear
            </button>
          </div>
        </div>
      </form>

      {failed && (
        <Banner tone="error">
          <span>Could not load trucks.</span>{" "}
          <button type="button" className="button" onClick={() => setRetry((n) => n + 1)}>
            Retry
          </button>
        </Banner>
      )}

      {trucks.length > 0 && (
        <div className="grid">
          {trucks.map((t) => (
            <TruckCard key={t.id} truck={t} mediaUrl={client.mediaUrl} />
          ))}
        </div>
      )}

      {!loading && !failed && !unauthorized && trucks.length === 0 && (
        <div className="empty">
          <p>No trucks saved for this range</p>
        </div>
      )}

      {hasMore && (
        <button
          type="button"
          className="button"
          disabled={loading}
          onClick={() => void fetchPage(offsetRef.current, applied, true)}
        >
          Load more
        </button>
      )}
    </>
  );
}
