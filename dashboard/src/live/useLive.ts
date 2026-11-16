import { useEffect, useRef, useState } from "react";
import { useApi } from "../apiContext";
import { ApiError, type ApiClient } from "../api/client";
import { connectLive, type LiveState } from "../api/live";
import type { Status, Truck } from "../api/types";

export interface UseLiveOptions {
  connect?: typeof connectLive;
  keep?: number;
  /** Called once for each truck the engine reports as new (not seen before in this list). */
  onNewTruck?: (truck: Truck) => void;
}

export interface LiveData {
  status: Status | null;
  trucks: Truck[];
  state: LiveState;
}

/** Newest `first_seen` first; ties go to the higher id. */
function compareTrucks(a: Truck, b: Truck): number {
  const diff = Date.parse(b.first_seen) - Date.parse(a.first_seen);
  return diff !== 0 && !Number.isNaN(diff) ? diff : b.id - a.id;
}

function merge(list: Truck[], incoming: Truck[], keep: number): Truck[] {
  const byId = new Map<number, Truck>();
  for (const t of list) byId.set(t.id, t);
  for (const t of incoming) byId.set(t.id, t);
  return [...byId.values()].sort(compareTrucks).slice(0, keep);
}

export function useLive(client: ApiClient, opts: UseLiveOptions = {}): LiveData {
  const { authEpoch, reportUnauthorized } = useApi();
  const connect = opts.connect ?? connectLive;
  const keep = opts.keep ?? 20;

  const [status, setStatus] = useState<Status | null>(null);
  const [trucks, setTrucks] = useState<Truck[]>([]);
  const [state, setState] = useState<LiveState>("connecting");

  const trucksRef = useRef<Truck[]>([]);
  const onNewRef = useRef(opts.onNewTruck);
  onNewRef.current = opts.onNewTruck;
  const reportRef = useRef(reportUnauthorized);
  reportRef.current = reportUnauthorized;

  useEffect(() => {
    let cancelled = false;
    const apply = (incoming: Truck[]) => {
      trucksRef.current = merge(trucksRef.current, incoming, keep);
      setTrucks(trucksRef.current);
    };

    // Messages that arrive before a fetch finishes win over the fetched copy.
    const fill = (replaceStatus: boolean) => {
      Promise.all([client.listTrucks({ limit: keep }), client.getStatus()])
        .then(([list, st]) => {
          if (cancelled) return;
          const known = new Set(trucksRef.current.map((t) => t.id));
          apply(list.trucks.filter((t) => !known.has(t.id)));
          setStatus((cur) => (replaceStatus ? st : (cur ?? st)));
        })
        .catch((e: unknown) => {
          if (!cancelled && e instanceof ApiError && e.status === 401) reportRef.current();
        });
    };
    fill(false);
    let prev: LiveState = "connecting";

    const stop = connect({
      url: () => client.liveUrl(),
      onMessage: (m) => {
        if (cancelled) return;
        if (m.type === "status") {
          setStatus(m.status);
        } else if (m.type === "truck.new") {
          const isNew = !trucksRef.current.some((t) => t.id === m.truck.id);
          apply([m.truck]);
          if (isNew) onNewRef.current?.(m.truck);
        } else if (m.type === "truck.updated") {
          apply([m.truck]);
        }
        // "telemetry" is for the map page; the live page ignores it.
      },
      onState: (s) => {
        if (cancelled) return;
        setState(s);
        // Back after a gap: backfill what was missed (quietly, no new-truck alerts).
        if (prev === "reconnecting" && s === "open") fill(true);
        prev = s;
        if (s === "unauthorized") reportRef.current();
      },
    });

    return () => {
      cancelled = true;
      stop();
    };
    // `authEpoch` is a dependency on purpose: every saved token (even the same one
    // again) means refetch and reconnect.
  }, [client, connect, keep, authEpoch]);

  return { status, trucks, state };
}
