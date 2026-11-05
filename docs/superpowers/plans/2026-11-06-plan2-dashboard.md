# sky-eye Plan 2 — Dashboard Website Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A mobile-friendly local website served by the engine at `/` with Live, Trucks and Status/Settings pages, plus the small engine changes the website needs.

**Architecture:** A React + TypeScript single-page app in `dashboard/`, built by Vite to `dashboard/dist`, served by the existing FastAPI engine (`/` → `index.html`, `/assets/*` → bundles). The app talks to the engine only through the documented API (`/api/*`, `/ws`, `/stream.mjpg`, `/media/*`), so it can later be hosted elsewhere (online phase) without a rewrite. Hash routes (`#/live`, `#/trucks`, `#/trucks/<id>`, `#/status`) avoid any server-side routing.

**Tech Stack:** React 19, Vite 8, TypeScript (latest; fall back to `~5.9` if `tsc -b` fails with Vite's types), Vitest + jsdom + Testing Library; Python side unchanged (FastAPI, pytest).

**Spec:** `docs/superpowers/specs/2026-11-05-sky-eye-phase1-design.md` (§5.7 API, §5.8 Dashboard). Follow-ups from Plan 1: `docs/superpowers/plan1-followups.md` (Plan 2 rows).

## Global Constraints

- Runtime npm dependencies are **only** `react` and `react-dom`. No router, UI kit, icon pack, CSS framework or chart library. Dev dependencies: `vite`, `@vitejs/plugin-react`, `typescript`, `@types/react`, `@types/react-dom`, `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`.
- The built site makes **no external network requests** (no CDN fonts/scripts/images) — it must work on a field laptop with no internet.
- Works at **375 px** wide with no horizontal page scroll; light and dark themes via `prefers-color-scheme`, colours as CSS custom properties on `:root`.
- Every fetch sends header `X-SkyEye-Token` when a token is stored; every URL used by `<img>`, `<video>`, `WebSocket` and the MJPEG stream gets `token=<token>` as a query parameter (browsers can't send headers there). Token stored in `localStorage` key `skyeye.token`; all storage access wrapped in try/catch.
- Times from the API are ISO-8601 UTC (`...Z`); the UI shows them in the **viewer's local time zone**.
- Every JSON body/message from the engine carries `"api": 1` (existing rule, also for new endpoints).
- `dashboard/node_modules/` and `dashboard/dist/` stay git-ignored; `package-lock.json` is committed.
- Python engine rules from Plan 1 still hold (see `docs/superpowers/plans/2026-11-05-plan1-engine-core.md` Global Constraints).

## Review Focus

1. **Engine restarted or Wi-Fi blip** → the Live page shows "Reconnecting…", the WebSocket reconnects with backoff, the MJPEG image recovers, and the latest-trucks strip has no duplicates. Test: Task 4 `reconnects with backoff`, Task 5 `dedupes trucks by id` and `stream image retries after error`.
2. **Token mode** → a missing/wrong token shows a token prompt (not a blank page or broken images); after saving, data loads. Test: Task 1 `test_ws_unauthorized_close_code_seen_by_client`, Task 4 `401 raises ApiError`, Task 7 `shows token prompt on 401`.
3. **Phone width (375 px)** → no horizontal scroll, video scales to width, nav usable by thumb. Test: Task 3 `layout has viewport meta and no fixed widths over 375px` (CSS check) + controller visual check in Task 8.
4. **Dashboard not built yet** → `GET /` returns helpful JSON telling the user to run the build, not a 404. Test: Task 1 `test_root_without_dashboard_explains_build`.
5. **Truck without a clip** (`clip_url: null`: skipped, failed or removed by retention) → detail view says "No clip saved" instead of a broken player; times are local. Test: Task 6 `shows no-clip message`, Task 4 `formatTime uses local zone`.

---

### Task 1: Engine serves the dashboard; `/api/info`; WebSocket auth close code

**Files:**
- Modify: `engine/config.py` (ApiConfig), `engine/api/app.py`, `engine/api/auth.py`, `engine/app.py` (build `info`)
- Test: `tests/test_api.py`, `tests/test_config.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `ApiConfig.dashboard_dir: Path | None = None` — `None` means `<repo>/dashboard/dist` computed as `Path(engine.__file__).resolve().parents[1] / "dashboard" / "dist"`; a relative value resolves against the config folder (add `("api", "dashboard_dir")` to `_PATH_FIELDS`).
  - `EngineContext.info: dict[str, Any]` (default empty dict) and `EngineContext.dashboard_dir: Path | None`.
  - `GET /api` → `{"api":1,"name":"sky-eye","version":"0.1.0"}` (the old root JSON moves here).
  - `GET /` → `index.html` (`FileResponse`, `Cache-Control: no-cache`) when `<dashboard_dir>/index.html` exists; otherwise `{"api":1,"name":"sky-eye","version":"0.1.0","dashboard":"not built - run: cd dashboard && npm ci && npm run build"}`.
  - `/assets/*` → `StaticFiles(<dashboard_dir>/assets)` mounted only when that folder exists.
  - `GET /api/info` → `{"api":1,"info":{...}}` with exactly: `version` (str), `source` (`{"type":"file"|"capture","name":str,"fps":float}` — file → **file name only**, capture → `"capture device <n>"`), `detector` (`{"model":"rfdetr-<size>","input_size":int,"device":"cuda"|"cpu","gpu":str|null,"classes":list[str]}`), `auth` (`"none"|"token"`), `dashboard` (bool: index.html exists).
  - `build_engine` fills `info`; `gpu` = `torch.cuda.get_device_name(0)` when device is cuda and available, else `None` (import torch lazily, never raise).
  - Auth middleware exempts `GET /` and `GET /assets/...` (the page shell is not sensitive; data stays protected). WebSocket rejection now sends `websocket.accept` then `websocket.close` code **4401**, so browsers see 4401 (Plan 1 follow-up).

- [ ] **Step 1: Write failing tests**

```python
def test_root_serves_dashboard_index(tmp_path)      # dist/index.html + dist/assets/app.js → GET / 200 text/html containing '<div id="root">'; GET /assets/app.js 200
def test_root_without_dashboard_explains_build(tmp_path)  # no dist → GET / 200 JSON, body["dashboard"] contains "npm run build"
def test_api_root_json(client)                      # GET /api → {"api":1,"name":"sky-eye","version":"0.1.0"}
def test_info_shape(client)                          # GET /api/info → keys exactly version, source, detector, auth, dashboard; source.name has no path separators
def test_token_mode_dashboard_shell_public(tmp_path, monkeypatch)  # token mode: GET / and GET /assets/app.js 200 without token; /api/info 401 without, 200 with
def test_ws_unauthorized_close_code_seen_by_client(...)  # live uvicorn on ephemeral port, token mode, websockets/httpx-ws client without token → close code 4401 (not HTTP 403)
def test_dashboard_dir_default_and_relative(tmp_path)    # config: default None → resolves to <repo>/dashboard/dist in build; relative "web" → <config dir>/web
def test_build_engine_info(tmp_path, monkeypatch)        # fake detector, file source "samples dir/clip.mp4" → info.source.name == "clip.mp4", detector.model == "rfdetr-small"
```

For the live WebSocket test use the existing ephemeral-port uvicorn helper in `tests/test_api.py` and a client that exposes the close code (`websockets` is installed with `uvicorn[standard]`); hard timeouts so it cannot hang.

- [ ] **Step 2: Run** `python -m uv run pytest tests/test_api.py tests/test_config.py tests/test_cli.py -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** the interfaces above. Keep `/media`, `/stream.mjpg`, `/ws`, `/api/*` behaviour unchanged otherwise.
- [ ] **Step 4: Run** the same command, then `python -m uv run pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** — `feat(api): serve dashboard, add /api/info, WS 4401 visible to browsers`

---

### Task 2: Engine event/clip tweaks the UI depends on

**Files:**
- Modify: `engine/events/manager.py`, `engine/events/evidence.py`
- Test: `tests/test_events.py`, `tests/test_evidence.py`, `tests/test_api.py` (URL expectations)

**Interfaces:**
- Produces:
  - `truck_to_json`: `snapshot_url` and `crop_url` end with `?v=<int(round(best_score * 10000))>` (cache-busting: the URL changes when a better snapshot replaces the file). `clip_url` unchanged.
  - `EventManager.on_frame`: when a better snapshot is saved inside the snapshot window, publish `truck.updated` for that truck (after releasing the manager lock).
  - `EventManager._create`: publish `truck.new` **before** `start_clip`, so a fast `truck.updated` can never arrive first.
  - Clips are written with MP4 `movflags=+faststart` (moov box before mdat) so browsers start playback immediately.

- [ ] **Step 1: Write failing tests**

```python
def test_snapshot_url_has_version(tmp_path)          # best_score .9 → snapshot_url endswith "?v=9000", crop_url too, clip_url has no "?"
def test_better_snapshot_publishes_update(tmp_path)  # score .5 then .9 within window → bus gets truck.new then truck.updated whose snapshot_url ends "?v=9000"
def test_truck_new_published_before_clip_start(tmp_path)  # spy recorder.start_clip records bus messages seen so far → already contains truck.new
def test_clip_is_faststart(tmp_path)                 # write_clip → parse top-level MP4 boxes: index("moov") < index("mdat")
```

- [ ] **Step 2: Run** `python -m uv run pytest tests/test_events.py tests/test_evidence.py -v` — Expected: FAIL.
- [ ] **Step 3: Implement.** Faststart via `av.open(str(path), "w", format="mp4", options={"movflags": "+faststart"})`. Update existing tests that compare snapshot/crop URLs exactly.
- [ ] **Step 4: Run** `python -m uv run pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** — `feat(events): cache-busting snapshot URLs, update on better snapshot, faststart clips`

---

### Task 3: Dashboard scaffold, layout and theme

**Files:**
- Create: `dashboard/package.json`, `dashboard/package-lock.json`, `dashboard/index.html`, `dashboard/vite.config.ts`, `dashboard/tsconfig.json`, `dashboard/src/main.tsx`, `dashboard/src/App.tsx`, `dashboard/src/router.ts`, `dashboard/src/styles.css`, `dashboard/src/test/setup.ts`
- Test: `dashboard/src/App.test.tsx`, `dashboard/src/router.test.ts`, `dashboard/src/styles.test.ts`
- Modify: `README.md` (Dashboard section)

**Interfaces:**
- Produces:
  - npm scripts: `dev` (vite), `build` (`tsc -b && vite build`), `test` (`vitest run`), `typecheck` (`tsc -b --noEmit` or equivalent). Vitest env `jsdom`, setup file imports `@testing-library/jest-dom/vitest`; test script sets `TZ=Asia/Kolkata` portably (e.g. in `vite.config.ts` `test.env` or `process.env.TZ` in setup before any Date use).
  - `vite.config.ts`: `base: "/"`, `build.outDir: "dist"`, dev `server.proxy` for `/api`, `/media`, `/stream.mjpg` → `http://127.0.0.1:8000` and `/ws` → `ws://127.0.0.1:8000` (`ws: true`).
  - `router.ts`: `type Route = {page:"live"} | {page:"trucks"} | {page:"truck"; id:number} | {page:"status"}`; `parseHash(hash: string): Route` (unknown/empty → live; `#/trucks/abc` → trucks); `useRoute(): Route` (listens to `hashchange`); `href(route: Route): string`.
  - `App.tsx`: header with product name "sky-eye", nav links Live / Trucks / Status (`aria-current="page"` on the active one), renders a placeholder per page (filled by Tasks 5–7). Bottom tab bar on narrow screens, top nav on wide.
  - `styles.css`: CSS custom properties on `:root` for colours/spacing/radius, dark values under `@media (prefers-color-scheme: dark)`, `body` has an explicit background, system font stack (no web fonts), no element with a fixed width > 375 px.
  - `index.html`: `<meta name="viewport" content="width=device-width, initial-scale=1">`, `<title>sky-eye</title>`, inline SVG favicon as a data URI, `<div id="root">`.
- Load the `frontend-design:frontend-design` skill before writing `App.tsx`/`styles.css` and follow it for the visual direction (calm, high-contrast "operations console": dark-friendly, one accent colour for "truck" highlights).

- [ ] **Step 1: Scaffold** with `npm create vite@latest` equivalent by hand (no template junk: no sample logos/counter), install the exact dependency list from Global Constraints, `npm ci` works from the lockfile.
- [ ] **Step 2: Write failing tests**

```ts
test("parseHash maps routes", () => {
  expect(parseHash("")).toEqual({ page: "live" });
  expect(parseHash("#/trucks")).toEqual({ page: "trucks" });
  expect(parseHash("#/trucks/12")).toEqual({ page: "truck", id: 12 });
  expect(parseHash("#/trucks/abc")).toEqual({ page: "trucks" });
  expect(parseHash("#/status")).toEqual({ page: "status" });
  expect(parseHash("#/nope")).toEqual({ page: "live" });
});
test("nav marks the active page", ...)       // render <App/> with location.hash "#/trucks" → link "Trucks" has aria-current="page"
test("layout has viewport meta and no fixed widths over 375px", ...)  // read index.html + styles.css as text: viewport meta present; no `width: <n>px` / `min-width: <n>px` with n > 375 outside media queries
```

- [ ] **Step 3: Run** `cd dashboard && npm test` — Expected: FAIL. Implement. Run `npm test`, `npm run typecheck`, `npm run build` — Expected: tests pass, typecheck clean, `dashboard/dist/index.html` and `dashboard/dist/assets/*.js` exist.
- [ ] **Step 4: README** — "Dashboard" section: build (`cd dashboard`, `npm ci`, `npm run build`), dev (`npm run dev` with the engine on :8000), token mode note (token is saved in the browser; URLs carry `?token=`, which can appear in access logs — use token mode on trusted networks only).
- [ ] **Step 5: Commit** — `feat(dashboard): Vite + React scaffold, hash router, responsive layout`

---

### Task 4: API client, token storage, live connection, formatting

**Files:**
- Create: `dashboard/src/api/types.ts`, `dashboard/src/api/client.ts`, `dashboard/src/api/live.ts`, `dashboard/src/api/token.ts`, `dashboard/src/format.ts`
- Test: `dashboard/src/api/client.test.ts`, `dashboard/src/api/live.test.ts`, `dashboard/src/api/token.test.ts`, `dashboard/src/format.test.ts`

**Interfaces:**
- Produces (`types.ts`, matching the engine JSON exactly):
  - `type SourceState = "starting" | "running" | "ended" | "error"`
  - `interface Status { source_state: SourceState; fps: number; frames_processed: number; trucks_now: number; trucks_total: number; started_at: string; telemetry_state: string }`
  - `interface Truck { id: number; run_id: number; track_id: number; first_seen: string; last_seen: string; best_score: number; snapshot_url: string | null; crop_url: string | null; clip_url: string | null; lat: number | null; lon: number | null; geo_error_m: number | null; geo_status: string }`
  - `interface Runtime { confidence: number; sahi_enabled: boolean }`
  - `interface Info { version: string; source: { type: "file" | "capture"; name: string; fps: number }; detector: { model: string; input_size: number; device: "cuda" | "cpu"; gpu: string | null; classes: string[] }; auth: "none" | "token"; dashboard: boolean }`
  - `type LiveMessage = { type: "status"; status: Status } | { type: "truck.new" | "truck.updated"; truck: Truck }`
- `client.ts`:
  - `class ApiError extends Error { readonly status: number }`
  - `interface ApiClient { getInfo(): Promise<Info>; getStatus(): Promise<Status>; listTrucks(q?: { from?: string; to?: string; limit?: number; offset?: number }): Promise<{ trucks: Truck[]; limit: number; offset: number }>; getTruck(id: number): Promise<Truck>; getRuntime(): Promise<Runtime>; putRuntime(patch: Partial<Runtime>): Promise<Runtime>; mediaUrl(path: string): string; streamUrl(): string; liveUrl(): string }`
  - `createClient(opts: { getToken: () => string | null; baseUrl?: string; fetchImpl?: typeof fetch; location?: { protocol: string; host: string } }): ApiClient` — unwraps `info`/`status`/`truck`/`runtime` envelopes; non-2xx → `ApiError(status, body.error ?? statusText)`; `mediaUrl` appends `token` with `?` or `&` correctly (keeps existing `?v=`); `liveUrl` builds `ws:`/`wss:` from the page protocol.
- `token.ts`: `loadToken(): string | null`, `saveToken(token: string | null): void` (key `skyeye.token`; null removes; storage errors swallowed).
- `live.ts`: `type LiveState = "connecting" | "open" | "reconnecting" | "unauthorized"`; `connectLive(opts: { url: () => string; onMessage: (m: LiveMessage) => void; onState: (s: LiveState) => void; WebSocketImpl?: typeof WebSocket; backoffMs?: number[] }): () => void` — default backoff `[1000, 2000, 5000, 10000]` (repeat last); close code 4401 → `"unauthorized"` and stop; malformed messages ignored; returned function closes and stops reconnecting. `url` is a function so a new token is used on reconnect.
- `format.ts`: `formatTime(iso: string, now?: Date): string` (local `HH:MM:SS` if same local day as `now`, else `D MMM HH:MM`), `formatDuration(fromIso: string, toIso: string): string` (`"<1 s"`, `"12 s"`, `"3 min 4 s"`), `formatScore(score: number): string` (`"87%"`).

- [ ] **Step 1: Write failing tests** (fake `fetch` and a fake `WebSocket` class with `open/message/close` helpers and fake timers)

```ts
test("sends token header and unwraps status", ...)     // getToken → "abc": fetch called with headers X-SkyEye-Token: abc; returns status object
test("401 raises ApiError", ...)                       // fetch 401 {"api":1,"error":"unauthorized"} → rejects ApiError status 401 message "unauthorized"
test("listTrucks builds query", ...)                   // {from:"2026-11-06T00:00:00.000Z", limit:20} → URL /api/trucks?from=...&limit=20 (encoded)
test("mediaUrl keeps version and adds token", ...)     // "/media/x.jpg?v=9000" + token "a b" → "/media/x.jpg?v=9000&token=a%20b"; no token → unchanged
test("liveUrl uses wss on https", ...)
test("reconnects with backoff", ...)                   // close 1006 → state reconnecting; after 1000 ms new socket; second close → 2000 ms
test("4401 stops reconnecting", ...)                   // close 4401 → "unauthorized", no new socket after 60 s fake time
test("ignores malformed messages", ...)
test("token storage survives throwing localStorage", ...)
test("formatTime uses local zone", ...)                // TZ Asia/Kolkata: "2026-11-06T04:30:00Z" with now same day → "10:00:00"
test("formatDuration", ...)                            // 0.4 s → "<1 s"; 12 s → "12 s"; 184 s → "3 min 4 s"
```

- [ ] **Step 2: Run** `npm test` — Expected: FAIL. **Step 3:** Implement. **Step 4:** `npm test` + `npm run typecheck` — Expected: pass.
- [ ] **Step 5: Commit** — `feat(dashboard): typed API client, token storage, live WebSocket with backoff`

---

### Task 5: Live page

**Files:**
- Create: `dashboard/src/apiContext.tsx`, `dashboard/src/live/useLive.ts`, `dashboard/src/live/LivePage.tsx`, `dashboard/src/live/StreamView.tsx`, `dashboard/src/live/sound.ts`, `dashboard/src/components/TruckCard.tsx`, `dashboard/src/components/Banner.tsx`
- Modify: `dashboard/src/App.tsx` (render LivePage; app-level client + token context)
- Test: `dashboard/src/live/useLive.test.tsx`, `dashboard/src/live/LivePage.test.tsx`, `dashboard/src/live/StreamView.test.tsx`

**Interfaces:**
- Consumes: Task 4 `ApiClient`, `connectLive`, `LiveMessage`, `format*`.
- Produces:
  - App-level context `useApi(): { client: ApiClient; token: string | null; setToken(t: string | null): void; unauthorized: boolean; reportUnauthorized(): void }` in `dashboard/src/apiContext.tsx` (Task 7 renders the prompt from `unauthorized`).
  - `useLive(client: ApiClient, opts?: { connect?: typeof connectLive; keep?: number }): { status: Status | null; trucks: Truck[]; state: LiveState }` — initial fill `listTrucks({ limit: 20 })` + `getStatus()`; `truck.new` prepends, `truck.updated` replaces by `id` (or prepends if unknown), list deduped by `id`, newest `first_seen` first, capped at `keep` (20); `status` messages replace status; `unauthorized` state → `reportUnauthorized()`.
  - `StreamView({ src }: { src: string })` — `<img alt="Live drone video with truck boxes">` full-width, `object-fit: contain`; on `error` shows "Video stream unavailable — retrying" and retries after 2 s with a cache-buster param.
  - `TruckCard({ truck, mediaUrl, compact? })` — crop image (lazy-loaded, alt "Truck #<id>"), local time, duration, score, a "clip" badge when `clip_url` is set; links to `href({page:"truck", id})`.
  - `Banner({ tone: "info" | "warn" | "error", children })`.
  - `sound.ts`: `loadSoundEnabled(): boolean` / `saveSoundEnabled(on: boolean)` (key `skyeye.sound`, default false), `beep(): void` (Web Audio, 880 Hz, 150 ms; no-op if unavailable).
  - `LivePage`: StreamView (`client.streamUrl()`), stat tiles **Trucks now**, **Total this run**, **FPS** (1 decimal), source state; banners: `state` reconnecting → "Reconnecting to the engine…", `source_state` `ended` → "Video finished — trucks stay browsable", `error` → "Video source error — check the engine log"; latest-trucks strip (horizontal scroll inside its own container on narrow screens); sound toggle button (`aria-pressed`) → beep on each `truck.new` when on.

- [ ] **Step 1: Write failing tests** (fake client + fake `connectLive` capturing callbacks)

```ts
test("dedupes trucks by id", ...)          // initial [t1]; truck.new t1 again; truck.updated t1' → one card, updated score shown
test("new trucks go first and list is capped", ...)  // keep 3, 5 truck.new → 3 newest
test("shows stat tiles from status messages", ...)   // status trucks_now 2, trucks_total 7, fps 28.46 → "2", "7", "28.5"
test("ended video banner", ...)
test("reconnecting banner", ...)
test("stream image retries after error", ...)        // fire error on img → message shown; advance 2 s → img src changed (cache-buster)
test("beeps on truck.new only when sound on", ...)   // mock beep
```

- [ ] **Step 2: Run** `npm test` — FAIL. **Step 3:** Implement. **Step 4:** `npm test`, `npm run typecheck`, `npm run build` — pass.
- [ ] **Step 5: Commit** — `feat(dashboard): live page with stream, counts and latest trucks`

---

### Task 6: Trucks page and truck detail

**Files:**
- Create: `dashboard/src/trucks/TrucksPage.tsx`, `dashboard/src/trucks/TruckDetail.tsx`, `dashboard/src/trucks/dateRange.ts`
- Modify: `dashboard/src/App.tsx` (routes `trucks`, `truck`)
- Test: `dashboard/src/trucks/TrucksPage.test.tsx`, `dashboard/src/trucks/TruckDetail.test.tsx`, `dashboard/src/trucks/dateRange.test.ts`

**Interfaces:**
- Consumes: `useApi`, `ApiClient.listTrucks/getTruck/mediaUrl`, `TruckCard`, `format*`, `Banner`.
- Produces:
  - `dateRange.ts`: `localDayRange(fromDate: string | "", toDate: string | ""): { from?: string; to?: string }` — inputs are `<input type="date">` values (`YYYY-MM-DD`, local); `from` = local midnight of fromDate as ISO UTC; `to` = local midnight of the day **after** toDate (end-exclusive, matches the API); empty → omitted.
  - `TrucksPage`: "From"/"To" date inputs + "Apply"/"Clear"; responsive grid of `TruckCard`s (1 column at 375 px, more on wider); page size 48 with a "Load more" button (hidden when a page returns < 48); empty state "No trucks saved for this range"; error state with retry; 401 → `reportUnauthorized()`.
  - `TruckDetail({ id })`: back link to Trucks; full snapshot image (alt "Truck #<id> full frame"); `<video controls playsInline preload="metadata">` from `mediaUrl(clip_url)` when present, otherwise "No clip saved for this truck" text; facts list: first seen, last seen (local), on screen for (`formatDuration`), best score, run/track ids, location "Unknown" when `geo_status` is `"unknown"` (Map is Plan 5); 404 → "Truck not found".

- [ ] **Step 1: Write failing tests**

```ts
test("localDayRange is end-exclusive local days", ...)  // TZ Asia/Kolkata, "2026-11-06","2026-11-06" → from "2026-11-05T18:30:00.000Z", to "2026-11-06T18:30:00.000Z"
test("loads first page and more", ...)                  // 48 then 10 → 58 cards, Load more hidden
test("applies date filter", ...)                        // listTrucks called with from/to from localDayRange, offset reset to 0
test("empty state", ...)
test("shows no-clip message", ...)                      // clip_url null → no <video>, text "No clip saved for this truck"
test("video uses token url", ...)                       // token "t" → video src ends "&token=t" or "?token=t"
test("truck not found", ...)                            // getTruck rejects ApiError 404 → "Truck not found"
```

- [ ] **Step 2: Run** `npm test` — FAIL. **Step 3:** Implement. **Step 4:** `npm test`, `npm run typecheck`, `npm run build` — pass.
- [ ] **Step 5: Commit** — `feat(dashboard): trucks gallery with date filter and truck detail`

---

### Task 7: Status/Settings page and token prompt

**Files:**
- Create: `dashboard/src/status/StatusPage.tsx`, `dashboard/src/components/TokenPrompt.tsx`
- Modify: `dashboard/src/App.tsx` (route `status`; render `TokenPrompt` when `unauthorized`)
- Test: `dashboard/src/status/StatusPage.test.tsx`, `dashboard/src/components/TokenPrompt.test.tsx`

**Interfaces:**
- Consumes: `useApi`, `ApiClient.getInfo/getStatus/getRuntime/putRuntime`, `saveToken`.
- Produces:
  - `StatusPage`: sections **Engine** (version, source type + name + fps, uptime from `started_at`, source state, frames processed, FPS), **Detector** (model, input size, device, GPU or "CPU"), **Settings** — confidence slider `min 0.05 max 0.95 step 0.05` with the value shown as a percent; sends `putRuntime({ confidence })` 400 ms after the last change; shows "Saved" / the API error message; reverts the slider to the server value on error. Status refreshes every 2 s while the page is visible (`document.visibilityState`).
  - `TokenPrompt`: modal dialog (`role="dialog"`, labelled "Access token required"), password-type input, "Save" stores via `setToken` and clears `unauthorized` (pages refetch), short help text "Find it as SKYEYE_API_TOKEN in the engine's .env file".

- [ ] **Step 1: Write failing tests**

```ts
test("shows engine and detector info", ...)          // info.detector.gpu "NVIDIA GeForce RTX 4050 Laptop GPU" shown; gpu null → "CPU"
test("debounces confidence save", ...)               // three changes within 400 ms → one putRuntime({confidence: 0.6})
test("reverts slider on save error", ...)            // putRuntime rejects ApiError 422 "invalid runtime settings" → message shown, slider back to 0.4
test("shows token prompt on 401", ...)               // getInfo rejects ApiError 401 → dialog "Access token required"
test("saving token retries", ...)                    // type "secret", Save → saveToken("secret"), getInfo called again, dialog gone
```

- [ ] **Step 2: Run** `npm test` — FAIL. **Step 3:** Implement. **Step 4:** `npm test`, `npm run typecheck`, `npm run build` — pass.
- [ ] **Step 5: Commit** — `feat(dashboard): status and settings page, token prompt`

---

### Task 8: End-to-end wiring check

**Files:**
- Create: `tests/test_dashboard_e2e.py` (marked `@pytest.mark.dashboard`, deselected by default like `gpu`; register the marker in `pyproject.toml` and add it to `addopts` deselection)
- Modify: `README.md` (quick start mentions building the dashboard first), `pyproject.toml` (register `dashboard` marker, deselect by default)

**Interfaces:**
- Consumes: Tasks 1–7.

- [ ] **Step 1: Write the test** — skips unless `dashboard/dist/index.html` exists; builds an engine with `FakeDetector` (scripted moving box) on a generated 6 s video, realtime, data dir in `tmp_path`, uvicorn on an ephemeral loopback port; asserts: `GET /` is HTML with `<div id="root">`; every `/assets/...` URL referenced by `index.html` returns 200; `/api/info` 200; a WebSocket client receives a `status` message and at least one `truck.new`; the truck's `crop_url` returns `image/jpeg`; shutdown completes within 5 s.
- [ ] **Step 2: Run** `cd dashboard && npm ci && npm run build`, then `python -m uv run pytest -m dashboard -v` — Expected: PASS. Then `python -m uv run pytest -q` and `cd dashboard && npm test` — all pass.
- [ ] **Step 3: Commit** — `test: end-to-end dashboard serving check`

(After this task the controller does a visual check in a real browser at desktop and 375 px widths, light and dark, against a live engine run on `samples/drone3.mp4`.)
