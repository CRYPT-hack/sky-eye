import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import type { Truck } from "../api/types";
import { useApi } from "../apiContext";
import Banner from "../components/Banner";
import TruckCard from "../components/TruckCard";
import { localDayRange } from "./dateRange";

const PAGE_SIZE = 48;

interface Range {
  from: string;
  to: string;
}

export default function TrucksPage() {
  const { client, token, reportUnauthorized } = useApi();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [applied, setApplied] = useState<Range>({ from: "", to: "" });
  const [trucks, setTrucks] = useState<Truck[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const requestId = useRef(0);
  const offsetRef = useRef(0);

  const fetchPage = useCallback(
    async (offset: number, range: Range, append: boolean) => {
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
    void fetchPage(0, applied, false);
    return () => {
      requestId.current++;
    };
  }, [fetchPage, applied, token, retry]);

  const clear = () => {
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

      {!loading && !failed && trucks.length === 0 && (
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
