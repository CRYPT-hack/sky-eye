import { useMemo, useSyncExternalStore } from "react";

/** The pages of the dashboard. Routes live in the URL hash, so the engine needs no server-side routing. */
export type Route = { page: "live" } | { page: "trucks" } | { page: "truck"; id: number } | { page: "status" };

const TRUCK_ID = /^[0-9]+$/;

/**
 * Map a `location.hash` value to a route.
 * Empty or unknown hashes go to the live page; a bad truck id goes to the trucks gallery.
 */
export function parseHash(hash: string): Route {
  const segments = hash.replace(/^#/, "").split("/").filter(Boolean);
  const [page, id, ...rest] = segments;

  switch (page) {
    case "trucks": {
      if (id !== undefined && rest.length === 0 && TRUCK_ID.test(id)) {
        const value = Number(id);
        if (Number.isSafeInteger(value)) return { page: "truck", id: value };
      }
      return { page: "trucks" };
    }
    case "status":
      return { page: "status" };
    default:
      return { page: "live" };
  }
}

/** The hash link for a route, e.g. `#/trucks/12`. */
export function href(route: Route): string {
  switch (route.page) {
    case "truck":
      return `#/trucks/${route.id}`;
    default:
      return `#/${route.page}`;
  }
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function currentHash(): string {
  return window.location.hash;
}

function serverHash(): string {
  return "";
}

/** The current route; re-renders when the hash changes (links, back/forward, typed URLs). */
export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, currentHash, serverHash);
  return useMemo(() => parseHash(hash), [hash]);
}
