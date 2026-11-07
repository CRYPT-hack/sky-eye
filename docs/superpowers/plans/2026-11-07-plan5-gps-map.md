# sky-eye Plan 5 — Telemetry, Geolocation + Map Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put every truck on a map: read drone position/attitude (MAVLink live, DJI `.SRT` for recorded flights), turn each truck's box into a ground lat/lon with an error radius, and show trucks plus the drone's track on a Map page.

**Architecture:** New `engine/telemetry/` (pose sources behind a `Telemetry` protocol with a time-indexed `PoseBuffer`) and `engine/geo.py` (pure flat-ground pinhole geolocation). The EventManager asks telemetry for the pose matching a truck's frame and stores lat/lon/error/status on the record (columns already exist). The API adds the drone's latest pose and recent track; the website gets a Leaflet Map page.

**Tech Stack:** pymavlink 2.4 (LGPL-3.0, used unmodified), stdlib parsing for DJI SRT, numpy for geometry, Leaflet 1.9 (BSD-2) on the website with OpenStreetMap tiles.

**Spec:** `docs/superpowers/specs/2026-11-05-sky-eye-phase1-design.md` §5.4 (telemetry + geolocation), §5.6 (Telegram map link), §5.8 (Map page).

## Global Constraints

- Geolocation = pinhole camera with configured horizontal FOV (`camera.hfov_deg`, default 82.0); ray through the box's **bottom-centre** pixel, rotated by gimbal pitch/roll + drone yaw, intersected with a **flat ground plane** at height-above-ground; result to lat/lon.
- Every location carries an error radius; camera ray at/above the horizon (depression ≤ 5°) or error > **200 m** → `geo_status: "unreliable"` (no exact point on the map); no pose within **0.5 s** of the frame → `"unknown"`.
- Telemetry problems never stop detection: telemetry runs on its own thread; missing/stale telemetry only changes `telemetry_state` and `geo_status`.
- Dashboard: Leaflet is the **only** new runtime dependency (ruling: Plan 2's "react/react-dom only" constraint gains this exception because the spec names Leaflet). Map tiles are the **only** external requests and only on the Map page; tile URL is configurable (`api.map_tile_url`, default `https://tile.openstreetmap.org/{z}/{x}/{y}.png`) with OSM attribution; when tiles fail (offline) the map still shows points on a blank background with a notice.
- Every engine JSON body/message carries `"api": 1`; Plan 2/3 rules hold (token on media/WS URLs, 375 px, local times, secrets never logged).

## Review Focus

1. **No telemetry configured** → everything works as today, `geo_status: "unknown"`, Map page explains "No drone position data". Test: Task 5 `test_no_telemetry_keeps_unknown`, Task 6 `map shows no-telemetry notice`.
2. **Telemetry link drops mid-flight** → `telemetry_state: "stale"` after 2 s, new trucks get `"unknown"`, recovers when messages return. Test: Task 3 `test_mavlink_stale_and_recover`.
3. **Camera looking near the horizon / very high altitude** → `"unreliable"` with a large radius, never a confident wrong point. Test: Task 4 `test_near_horizon_unreliable`, `test_error_over_200m_unreliable`.
4. **DJI SRT format variants** (new `[latitude: ..]` style and older `GPS(lon,lat,alt)` style, with/without gimbal fields) → parsed or cleanly ignored per line. Test: Task 2 `test_parse_new_format`, `test_parse_legacy_format`, `test_garbage_lines_skipped`.
5. **Offline laptop in the field** → Map page still renders markers with coordinates when tiles can't load. Test: Task 6 `tile error shows offline notice`.

---

### Task 1: Telemetry core

**Files:** Create `engine/telemetry/__init__.py`, `engine/telemetry/core.py`; Modify `engine/config.py`; Test `tests/test_telemetry_core.py`, `tests/test_config.py`.

**Interfaces:**
- `@dataclass(frozen=True) DronePose(ts: float, lat: float, lon: float, alt_agl_m: float, yaw_deg: float, cam_pitch_deg: float, cam_roll_deg: float = 0.0)` — field names as in the spec §3; `ts` in the telemetry source's time base (see below); yaw clockwise from true north; pitch −90 = straight down.
- The spec's `Telemetry.pose_at(ts)` becomes `pose_for(frame)` here: recorded and live sources use different time bases (video position vs wall clock), so the source picks the frame field it matches on.
- `PoseBuffer(max_age_s: float = 0.5, capacity: int = 600)`: `add(pose)`, `nearest(t: float) -> DronePose | None` (closest by |Δt| ≤ max_age_s), `latest() -> DronePose | None`, `track(since_t: float) -> list[DronePose]` (thinned to ≤ 1 point/s). Thread-safe.
- `class Telemetry(Protocol)`: `start()`, `stop()`, `pose_for(frame: Frame) -> DronePose | None`, `latest() -> DronePose | None`, `track(seconds: float) -> list[DronePose]`, property `state -> str` (`"none" | "waiting" | "ok" | "stale"`).
- `NoTelemetry` (state `"none"`, everything None/empty).
- Config: `TelemetryConfig(type: Literal["none","mavlink","dji_srt"]="none", connection: str = "udpin:0.0.0.0:14550", srt_path: Path | None = None, max_age_s: float = 0.5 (>0), stale_after_s: float = 2.0 (>0))`, `CameraConfig(hfov_deg: float = 82.0 (10–170))` on `AppConfig.telemetry` / `AppConfig.camera`; `srt_path` resolves relative to the config folder; `dji_srt` requires a file source and defaults `srt_path` to the video path with suffix `.SRT` (case-insensitive lookup), ConfigError if missing.
- Config: `ApiConfig.map_tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"` (must contain `{z}`, `{x}`, `{y}`; must be `http(s)://`), used by Task 5's `/api/info` and Task 6's map.
- Tests: nearest within/outside window, capacity eviction, track thinning, thread-safety smoke, config validation + srt default lookup.
- Commit: `feat(telemetry): pose buffer, telemetry protocol, config`

### Task 2: DJI SRT telemetry (recorded flights)

**Files:** Create `engine/telemetry/dji_srt.py`; Test `tests/test_dji_srt.py` (fixtures as inline strings, no real flight files).

**Interfaces:**
- `parse_srt(text: str) -> list[DronePose]` — per subtitle block: start time → `t` (seconds from video start); fields from either format: new (`[latitude: 22.5] [longitude: 113.9] [rel_alt: 50.2 abs_alt: 120.0]`, optional `[gb_yaw: ..] [gb_pitch: ..] [gb_roll: ..]`, drone yaw from `[yaw: ..]` or `gb_yaw`) or legacy (`GPS(113.9,22.5,18)` + `H 50.2m` / `HS`); missing gimbal pitch → `-90.0` (assume nadir) with a one-time INFO; blocks without lat/lon/alt skipped.
- `DjiSrtTelemetry(path: Path, max_age_s: float, loop: bool = False)` — loads once on `start()`; `pose_for(frame)` uses `frame.ts` (video position); a looping file source keeps counting `frame.ts` up across loops (`engine/sources/file.py`), so with `loop=True` the lookup time is `frame.ts % period` where `period` = end time of the last subtitle block (no wrap when `loop=False`); state `"ok"` if any pose parsed else `"stale"`; reads with `utf-8-sig` and `errors="replace"`.
- Tests: `test_parse_new_format`, `test_parse_legacy_format`, `test_gimbal_missing_defaults_nadir`, `test_garbage_lines_skipped`, `test_pose_for_uses_video_time`, `test_pose_for_wraps_when_looping`.
- Commit: `feat(telemetry): DJI SRT flight log parser`

### Task 3: MAVLink telemetry (live)

**Files:** Create `engine/telemetry/mavlink.py`; Modify `pyproject.toml` (`pymavlink~=2.4`), `uv.lock`; Test `tests/test_mavlink.py`.

**Interfaces:**
- `MavlinkTelemetry(connection: str, max_age_s: float, stale_after_s: float, conn_factory=pymavlink.mavutil.mavlink_connection, clock=time.time)` — background thread `recv_match(blocking=True, timeout=0.5)`; combines `GLOBAL_POSITION_INT` (lat/lon ×1e-7, `relative_alt` mm → m, `hdg` cdeg if valid) + `ATTITUDE` (yaw rad) + gimbal (`GIMBAL_DEVICE_ATTITUDE_STATUS` quaternion → pitch/roll, else `MOUNT_STATUS` cdeg) into a `DronePose` stamped with **wall-clock** receive time; `pose_for(frame)` matches on `frame.wall_time.timestamp()`; state `"waiting"` before first position, `"ok"`, `"stale"` after `stale_after_s` without a position; reconnects on connection errors with the Plan 3 `Backoff`; never logs connection strings containing credentials.
- Tests (fake connection yielding scripted pymavlink message objects; one loopback test encoding real MAVLink over UDP on 127.0.0.1 ephemeral ports with timeouts): `test_combines_position_attitude_gimbal`, `test_quaternion_gimbal_to_pitch`, `test_mavlink_stale_and_recover`, `test_pose_for_matches_wall_time`, `test_udp_loopback_real_messages`.
- Commit: `feat(telemetry): MAVLink live telemetry`

### Task 4: Geolocation math

**Files:** Create `engine/geo.py`; Test `tests/test_geo.py`.

**Interfaces:**
- `@dataclass(frozen=True) GeoFix(lat: float | None, lon: float | None, error_m: float | None, status: str)` (`"ok" | "unreliable" | "unknown"`).
- `geolocate(box: tuple[float,float,float,float], frame_size: tuple[int,int], pose: DronePose | None, hfov_deg: float) -> GeoFix` — bottom-centre pixel; pinhole intrinsics from hfov and frame width (square pixels); camera frame rotated by roll, pitch (gimbal) and drone yaw (ENU); intersect with ground z = −alt_agl; local tangent-plane offset → lat/lon (WGS-84 radius-of-curvature approximation); depression angle ≤ 5° or no intersection → `"unreliable"` with `lat/lon` None; error model `error_m = 3.0 + slant_range * tan(radians(2.0)) / max(sin(depression), 0.05)`; `> 200` → `"unreliable"` (keep lat/lon for the circle); `pose is None` → `"unknown"`.
- Tests (hand-computed): `test_nadir_centre_is_below_drone`, `test_nadir_offset_pixel` (known metres east/north at 100 m alt, hfov 90°), `test_yaw_rotates_offset`, `test_45deg_pitch_forward_distance` (≈ alt), `test_near_horizon_unreliable`, `test_error_over_200m_unreliable`, `test_no_pose_unknown`.
- Commit: `feat(geo): flat-ground pinhole geolocation with error radius`

### Task 5: Engine wiring + API + Telegram map link

**Files:** Modify `engine/app.py`, `engine/pipeline.py`, `engine/events/manager.py`, `engine/api/app.py`, `engine/alerts/telegram.py`; Test `tests/test_events.py`, `tests/test_api.py`, `tests/test_cli.py`, `tests/test_alerts_channels.py`.

**Interfaces:**
- `build_telemetry(cfg: AppConfig) -> Telemetry` (passes `cfg.source.loop` to `DjiSrtTelemetry`); started/stopped with the pipeline.
- `EventManager(..., telemetry: Telemetry = NoTelemetry(), hfov_deg: float = 82.0)` — on truck creation and on each better snapshot: `geolocate(...)` with `telemetry.pose_for(frame)` and the frame size; stores lat/lon/geo_error_m/geo_status; published in `truck.new`/`truck.updated`.
- `PipelineStatus.telemetry_state` from `telemetry.state`.
- `GET /api/telemetry?seconds=300` → `{"api":1,"state":"ok","latest":{lat,lon,alt_agl_m,yaw_deg,t_iso}|null,"track":[[lat,lon],...]}` (track thinned, ≤ 600 points); WS adds `{"api":1,"type":"telemetry","latest":{...}}` at most once per second when it changes.
- `/api/info` adds `"telemetry": "<type>"` and `"map_tile_url"` (no secrets; MAVLink connection string never exposed).
- Telegram caption adds `https://www.openstreetmap.org/?mlat=<lat>&mlon=<lon>#map=17/<lat>/<lon>` when `geo_status == "ok"`.
- Tests: `test_no_telemetry_keeps_unknown`, `test_truck_gets_location_from_pose`, `test_unreliable_location_stored`, `test_telemetry_endpoint_and_ws`, `test_info_has_no_connection_string`, `test_telegram_caption_has_map_link`.
- Commit: `feat: geolocate trucks from telemetry; telemetry API`

### Task 6: Dashboard Map page

**Files:** Modify `dashboard/package.json` (+ `leaflet`, dev `@types/leaflet`), `dashboard/src/router.ts` (`{page:"map"}` → `#/map`), `dashboard/src/App.tsx` (nav item "Map"), `dashboard/src/api/*` (types + `getTelemetry()`), `dashboard/src/trucks/TruckDetail.tsx` (location row + "Open in OpenStreetMap" link when ok), `dashboard/src/status/StatusPage.tsx` (telemetry row); Create `dashboard/src/map/MapPage.tsx`, `dashboard/src/map/leafletView.ts`; Tests alongside.

**Interfaces:**
- Leaflet CSS imported from the package (bundled, not a CDN); marker icons drawn as `L.circleMarker` (no image assets); tile layer URL from `info.map_tile_url` with OSM attribution text.
- Map page: date range filter (reuse `localDayRange`), trucks with `geo_status: "ok"` as circle markers coloured by age (spec §5.8 "colour = age": three buckets — last hour, earlier today, older — with a small legend; popup: crop thumbnail, time, link to `#/trucks/<id>`), `"unreliable"` as dashed circles of radius `geo_error_m`, drone latest position + track polyline (live via WS `telemetry`), "fit to data" button; notices: no telemetry configured / no located trucks / tiles unavailable (Leaflet `tileerror` → banner "Map tiles unavailable — showing points only").
- Tests (Leaflet mocked at the `leafletView.ts` boundary so jsdom never needs canvas): `map shows no-telemetry notice`, `renders ok and unreliable trucks differently`, `drone track updates from live messages`, `tile error shows offline notice`, `nav includes Map`, detail page location row.
- `npm test`, `npm run typecheck`, `npm run build` pass; 375 px no horizontal scroll (map is full-width, fixed height via CSS).
- Commit: `feat(dashboard): map page with trucks and drone track`

### Task 7: Docs + end-to-end

- README "GPS & map": DJI (enable flight-log subtitles, keep the `.SRT` next to the video), PX4/ArduPilot (MAVLink UDP from the companion/telemetry radio, `connection: udpin:0.0.0.0:14550`), camera FOV setting, accuracy expectations (flat-ground assumption, error circle), offline maps note.
- E2E (`-m dashboard`): generated video + generated SRT with a drone at known lat/lon looking straight down → the truck's `lat/lon` within 5 m of the expected point; `/api/telemetry` returns a track; Map page assets load.
- Commit: `docs: GPS and map; e2e geolocation check`
