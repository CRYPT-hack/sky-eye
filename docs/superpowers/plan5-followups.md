# Plan 5 (GPS + map): follow-ups

Plan 5 is complete and reviewed, including a final whole-branch review and one fix wave. This file lists what was deliberately left open.

## Before the online dashboard (Tailscale + logins)

- **Tile URLs over HTTPS.** If the dashboard is served over HTTPS, an `http://` `api.map_tile_url` becomes mixed content and the browser blocks the tiles. Require or recommend `https://` tile URLs then.
- **MAVLink from any LAN host.** `udpin:0.0.0.0:<port>` accepts packets from any machine on the network, so someone on the same Wi-Fi could spoof the drone's position. Prefer `udpin:127.0.0.1:<port>` with ground-station forwarding (the README's same-laptop setup), or add a source-address allowlist.
- **Tile API keys are browser-visible.** `map_tile_url` is served by `/api/info` and stored in the run record. Keys for paid tile providers end up in every viewer's browser. The README warns about this; a server-side tile proxy would hide them.

## Accuracy (later plans)

| Plan | Item |
|---|---|
| 4/6 | Gimbal yaw relative to the drone (GIMBAL_DEVICE_ATTITUDE_STATUS yaw, MOUNT_STATUS `pointing_c`, DJI `gb_yaw` when relative) is not modelled beyond preferring `gb_yaw`, so a gimbal panned sideways mislocates. The README warns. |
| 6 | Terrain and buildings: the flat-ground assumption is exact only over level ground at take-off height. A DEM lookup would remove most of the hill error. |
| 6 | Lens distortion: wide drone lenses bend straight lines near the edges. Per-model distortion coefficients would tighten edge-of-frame fixes. |
| 6 | Video-to-telemetry latency and drone speed are not in the error model; at 15 m/s × 0.3 s that is about 5 m. |
| 6 | Several MAVLink systems on one link (e.g. a second vehicle) are mixed into one track. Filter by system id. |
| 6 | A backward wall-clock step can leave an older MAVLink pose as `latest()` for the length of the step. |

## Small residual fixes (any time)

- Map page: the quiet reconnect backfill lets on-screen rows win over freshly fetched rows, so `truck.updated` messages missed during the gap aren't applied. A reconnect during the very first load drops the auto-fit and swallows a load failure. Fix both by preferring fetched rows except ids received live since the reload started, and by going quiet only after one load succeeded (`dashboard/src/map/MapPage.tsx`).
- Map page: the "Showing the first 1000 trucks" notice also appears when exactly 1000 trucks come back.
- Events: an "unreliable" fix without a position can replace an "unreliable" fix that had one (`engine/events/manager.py`, the rank check), so the truck loses its approximate circle. Prefer the fix that has a position when the rank is equal.
- DJI SRT: the broad `except` reports parser bugs as "cannot read SRT file"; `load()` check-then-parse is not atomic (a harmless double parse); `track()` copies the tail of the file on each call (only on page open/reconnect).
- MAVLink: `validate_connection` returns the unstripped string (YAML strips unquoted values anyway); MOUNT_STATUS is read before the quaternion check; a zero or non-unit quaternion isn't normalised.
- Geo: `hfov`/`frame_size` aren't validated inside `geolocate` (config bounds and the EventManager guard cover it); `_ray_enu` parameters are untyped.
- Engine: the telemetry-failure rate-limit test patches the process-wide `time.monotonic`; a hung telemetry stop can add up to 2 s after the thread joins; pose and geolocation failures share one rate-limit state.
- Tests: Task 1 tie test over-specified; untested PoseBuffer eviction of a late oldest pose and duplicate ts; untested DJI period ≤ 0 and mid-flight track; the `TEL_SRT` string and cross-test import of `write_config`; e2e asset regexes depend on Vite chunk names.
- `config.example.yaml`: the new `telemetry`, `camera` and `api` keys are live with their defaults rather than commented examples (behaviour is unchanged because `type` is `none`).
