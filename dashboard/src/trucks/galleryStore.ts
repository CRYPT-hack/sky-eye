import type { ApiClient } from "../api/client";
import type { Truck } from "../api/types";

/** The applied date filter: `<input type="date">` values, "" for no bound. */
export interface DayRange {
  from: string;
  to: string;
}

/** What the trucks gallery shows; kept while the user looks at a truck's detail page. */
export interface GalleryState {
  applied: DayRange;
  trucks: Truck[];
  /** Offset of the next page to load. */
  offset: number;
  hasMore: boolean;
  scrollY: number;
}

interface Saved extends GalleryState {
  client: ApiClient;
  authEpoch: number;
}

// Module-level on purpose: the gallery unmounts on every route change, the store does not.
let saved: Saved | null = null;

/** The saved gallery, or null when there is none for this client and auth epoch (a new token resets it). */
export function loadGallery(client: ApiClient, authEpoch: number): GalleryState | null {
  if (saved && saved.client === client && saved.authEpoch === authEpoch) return saved;
  saved = null;
  return null;
}

export function saveGallery(client: ApiClient, authEpoch: number, state: GalleryState): void {
  saved = { ...state, client, authEpoch };
}

export function clearGallery(): void {
  saved = null;
}
