# Plan 2 (dashboard website): follow-ups

Plan 2 is complete and reviewed. This file lists what was deliberately left open.

## Worth doing soon

- **The Trucks gallery shows a stale list when you return from Live or Status.** The saved gallery state is restored without a refetch, so newly saved trucks only appear after Apply, Clear or a reload. Fix: restore the saved state only when coming back from a truck detail page, or refresh the first page in the background (`dashboard/src/trucks/TrucksPage.tsx`).
- **Accessibility pass.** Move focus to, or announce, the page title on route changes. Keep live regions (banners, stream messages) mounted at all times so screen readers catch them.
- **Phone token hand-off.** A long random token is painful to type on a phone; consider a QR code or `#token=` link printed by the engine.

## Engine items for later plans

| Plan | Item |
|---|---|
| 3 | `last_seen` / "On screen for" only update when a track ends; a live `truck.updated` heartbeat (or periodic `last_seen` writes) would keep the detail page current |
| 3 | An engine `clip_status` (`pending / saved / skipped / removed`) would replace the client's 120 s "Clip is being saved…" heuristic, which also depends on phone and engine clocks agreeing |
| 4/6 | Many very short tracks on aerial footage (detector flicker), see Plan 1 follow-ups |
| 6 | Measure FPS on an idle, plugged-in machine (≈3.5 FPS observed on a loaded laptop on 6 Nov) |

## Small residual fixes (any time)

- Gallery store: a navigation processed in the same React batch as an unrendered response can save a new offset with old rows (few-ms window).
- `StreamView`: call `sync()` once after subscribing to `visibilitychange` so a change between first render and subscription isn't missed.
- Windows only: if the full snapshot is replaced but the crop's rename retries run out, the pair can mismatch until the next better frame.
- If `dist/` is emptied mid-request during a rebuild, `GET /` can return a plain-text 500.
- Status page maps every `TypeError` (not only network failures) to "Cannot reach the engine".
- Deferred task-level minors from the Plan 2 reviews: "reconnecting" emitted twice per reconnect; `withToken` ignores `#fragment`; `formatTime` on invalid input; no `from > to` guard on the date filter; no "Loading…" text on Trucks; old list kept under the error banner after a failed filter change; `.dialog::backdrop` uses a literal colour; `#trucks/0` accepted; `fakes.ts` lives in `src/live`.
