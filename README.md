# sky-eye

Drone truck-detection engine: reads a video source, detects and tracks trucks
with RF-DETR, saves a snapshot, crop and short clip per truck, and serves it all
over a small HTTP API with a live MJPEG stream.

## Requirements

- Windows/Linux with an NVIDIA GPU and a recent NVIDIA driver: the default PyTorch
  build is CUDA 13.0 (`cu130`), which needs a driver that supports CUDA 13. Without
  a GPU, set `detector.device: cpu` (slow).
- [uv](https://docs.astral.sh/uv/). If `uv` is installed but not on your PATH, use
  `python -m uv` everywhere below instead of `uv` (e.g. `python -m uv sync`).
- Disk and internet for the first run: `uv sync` downloads about 3 GB (PyTorch with
  CUDA), and the first start downloads the RF-DETR weights into `~/.roboflow/models`
  (override with the `RF_HOME` environment variable).

## Quick start

1. Install the dependencies: `uv sync`
2. Copy the example config: `cp config.example.yaml config.yaml`
3. Optional, only for token auth: copy `.env.example` to `.env` next to `config.yaml`
   and set `SKYEYE_API_TOKEN` to a long random string.
4. Build the website once: `cd dashboard`, `npm ci`, `npm run build`, then `cd ..`
   back to the repo root. Needs npm and Node.js 22.13 or newer on the 22 line, Node 24,
   or Node 26 and newer. The engine serves `dashboard/dist/` and cannot show the site
   without it.
5. Put a drone clip at `samples/drone.mp4` (or change `source.path` in `config.yaml`).
6. Optional: check the setup without starting anything (also downloads the weights):
   `uv run python -m engine --config config.yaml --check`
7. Run: `uv run python -m engine --config config.yaml`
8. Open the printed `Dashboard: http://<ip>:8000/` URL. The raw endpoints also work:
   `/stream.mjpg` for the live annotated video, `/api/trucks` for detected trucks and
   `/api/status` for FPS.

Evidence is written to `data/media/<date>/` (`_full.jpg`, `_crop.jpg`, `.mp4` per
truck); logs (including the HTTP server's) to `data/logs/sky-eye.log`. Press Ctrl+C
to stop; clips that are not finished yet are dropped, and so are alerts still held
back by the cooldown, so the stop is quick. If the engine was killed instead, its
unfinished clips show as "Clip could not be saved" after the next start. For a file
source the API keeps serving after the video ends so you can browse the trucks.

To reach the API from a phone on the same Wi-Fi, allow Python through the Windows
firewall on **Private** networks only. Set `api.auth.mode: token` and
`SKYEYE_API_TOKEN` (in the environment or a `.env` next to `config.yaml`) to require a token.

Exit code 2 with a short message means a startup problem (bad config value, port
already in use, missing weights, no CUDA). Exit code 1 means the engine did not stop
within 5 s and was ended forcibly.

## Live drone input

Set `source.type` in `config.yaml`. Every source is read the same way once it is open;
the website's Status page shows the source state.

- **HDMI capture card (works with any drone):** plug the controller's HDMI out into a
  USB capture card and use `type: capture` with `device: 0` (try 1, 2, ... if you
  have several cameras). The card keeps receiving the controller's screen when the
  drone's video link drops, so the engine keeps getting frames and "Drone video lost"
  only appears when the card itself stops delivering (e.g. unplugged).
- **DJI apps:** if your DJI app offers live streaming to a custom RTMP server, run one
  relay on the laptop: [MediaMTX](https://github.com/bluenviron/mediamtx) with its
  default settings accepts RTMP on port 1935 and serves the same stream over RTSP on
  port 8554. Point the app's "RTMP custom" option at
  `rtmp://<laptop-ip>:1935/live/drone`, allow MediaMTX through the Windows firewall
  on **Private** networks (so the controller or phone can reach port 1935), and use:

  ```yaml
  source:
    type: stream
    url: rtsp://127.0.0.1:8554/live/drone   # or rtmp://127.0.0.1:1935/live/drone
  ```

  The exact menu names and what is supported depend on your app and drone; this
  project does not promise anything for a specific model.

  If you already use [go2rtc](https://github.com/AlexxIT/go2rtc) instead, name the
  stream `drone` in `go2rtc.yaml` and use `type: go2rtc` (with `go2rtc.name: drone`;
  `go2rtc.rtsp_base` defaults to `rtsp://127.0.0.1:8554`); see go2rtc's README for
  how to feed it. Run only one of the two relays: both use RTSP port 8554 by default.
- **RTSP cameras, PX4 companion computers, other streams:**

  ```yaml
  source:
    type: stream
    url: rtsp://192.168.1.20:8554/live
  ```

  Accepted schemes: `rtsp`, `rtsps`, `rtmp`, `rtmps`, `udp`, `http`, `https`.
  `srt://` is not read directly: MediaMTX can ingest SRT natively (see its
  documentation), and sky-eye then reads the stream from it over RTSP
  (`rtsp://127.0.0.1:8554/<path>`).
  Passwords in the URL are hidden in logs and on the website; a password containing
  `#`, `/` or `?` must be percent-encoded (`%23`, `%2F`, `%3F`).

**Connecting** means no frame has arrived yet and the first attempt to reach the
stream failed (the relay is not running, or the drone is not streaming yet). The
engine keeps retrying; the website shows a "Waiting for drone video…" banner. During
a normal start the label reads "Starting" until the first frame.

**Reconnecting** means the stream stopped delivering frames (Wi-Fi drop, drone out of
range, encoder restart). The engine keeps running and retries with a growing delay;
the website shows a "Drone video lost — reconnecting…" banner (the status label reads
"Reconnecting"; the separate "Reconnecting to the engine…" banner means the browser lost
its link to the engine, not the drone) and detection resumes by itself when
frames return.

## Alerts

When a new truck is confirmed, the engine can alert you on up to three channels. In
`config.yaml`:

```yaml
alerts:
  cooldown_s: 10      # trucks within this many seconds of the last alert become one batch
  desktop: true       # Windows toast and beep on the laptop running the engine (default on)
```

On Windows the toast is shown by a separate helper thread, so a toast that fails
cannot be noticed or retried by the engine; its error is printed to the console
(stderr), not to the engine log.

**Telegram**

1. Talk to @BotFather in Telegram and run `/newbot` to get a token.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<token>/getUpdates` in a browser (replace
   `<token>` with your token) and find `chat` then `id` in the reply.
4. Put both in `.env`: `TELEGRAM_BOT_TOKEN=...` and `TELEGRAM_CHAT_ID=...`.
5. In `config.yaml` set `alerts.telegram.enabled: true`, and optionally
   `alerts.telegram.dashboard_url` to the LAN URL printed at startup so the message links
   to the website.

**MQTT**

```yaml
alerts:
  mqtt:
    enabled: true
    host: 127.0.0.1
    port: 1883
    topic: sky-eye/events
    username: null     # set a user name to authenticate; the password is MQTT_PASSWORD in .env
    tls: false         # true usually also needs port: 8883
```

Delivery is at-least-once (QoS 1), so a subscriber can see the same event twice.
Without `tls: true` the user name and password travel unencrypted, so use them
without TLS only on a network you trust. Most brokers serve TLS on port 8883, not 1883.

Secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `MQTT_PASSWORD`) belong in `.env`
only, never in `config.yaml`.

On the website's Status page, each configured channel has a switch that turns it on or
off while the engine runs (not saved to `config.yaml`; it resets on restart), and
**Send test alert** queues a test message for every channel that is switched on. If
nothing arrives, the engine log says why (for example a wrong chat id or an
unreachable broker). A failing channel is retried a few times and never stops
detection.

## GPS & map

With the drone's position, height and camera angle (together called **telemetry**),
sky-eye works out where each truck stands on the ground and shows it on the website's
Map page and as a map link in Telegram alerts. Without telemetry everything else works
as before, and trucks show "Location: Unknown".

**DJI drones (recorded flights)**

1. In the DJI app (DJI Fly / DJI GO) turn on the flight-log subtitles: the setting is
   called "Video Caption" or "Video Subtitles". The drone then writes a `.SRT` file
   next to every video.
2. Keep the `.SRT` next to the video with the same name (`flight.MP4` and `flight.SRT`).
3. Set it up in `config.yaml`:

   ```yaml
   source:
     type: file
     path: samples/flight.MP4
   telemetry:
     type: dji_srt     # srt_path is optional: the .SRT next to the video is found by itself
   ```

   `telemetry.srt_path` is only needed when the file lives somewhere else. This works
   for `source.type: file` only.

   Many consumer DJI logs have no camera (gimbal) angle, and some have no heading
   (the compass direction the drone or camera faces). Without a camera angle, truck
   locations are marked "unreliable" (a dashed circle of at least 200 m) unless you
   tell sky-eye the angle. If you filmed straight down, add:

   ```yaml
   camera: { pitch_deg: -90 }   # allowed: -90 (straight down) to 0 (level with the horizon)
   ```

   `camera.pitch_deg` only fills in when the log gives no camera angle; a real angle in
   the log always wins. A log without a heading still gives locations, but the error
   circle gets wider.

**PX4 / ArduPilot drones (live)**

sky-eye only listens to the flight controller (MAVLink is the protocol they speak);
it never sends commands to the drone.

- **Never point sky-eye at the UDP port your ground station itself listens on.** Only
  one program can listen on a port, so the second one fails ("UDP port already in use"
  in the engine log). QGroundControl listens on 14550, so do not use 14550 when it runs
  on the same laptop.
- **Ground station (QGroundControl or Mission Planner) on the same laptop:** let it
  forward MAVLink to another port and listen on that one. In QGroundControl:
  Application Settings, then MAVLink, enable forwarding and set the target to
  `localhost:14445`. For Mission Planner, use its MAVLink output/forwarding to a UDP
  port. Then:

  ```yaml
  telemetry:
    type: mavlink
    connection: "udpin:127.0.0.1:14445"   # the forwarding port, not 14550
  ```

- **No ground station on the laptop** (a telemetry radio or companion computer sends
  straight to the laptop): `connection: "udpin:0.0.0.0:14550"` is fine.
- **Serial telemetry radio:** for example `connection: "COM5,57600"` (port, speed; on
  Linux `/dev/ttyUSB0,57600`). A serial port can be open in only one program: either
  the ground station or sky-eye. If it is taken, the log says "serial port is open in
  another program".
- Only `udp:` / `udpin:` (listening) and serial links are accepted. `udpout:` and
  `udpbcast:` are refused because sky-eye never sends anything.
- ArduPilot: if nothing arrives, the telemetry stream rates (the `SRx_*` parameters)
  may be 0; set them in your ground station.
- Allow Python through the Windows firewall on **Private** networks for the UDP port
  you actually use (14445 or 14550 above).

**Camera field of view.** The calculation needs the horizontal field of view (how wide
the camera sees, in degrees) of the video:

```yaml
camera:
  hfov_deg: 82      # default
```

Look the value up in your drone's spec sheet; typical wide cameras are around 70 to 85
degrees (for example 82 to 84 for many DJI models; the spec sheet usually gives the
diagonal field of view, so check which one it is). A zoomed or cropped video needs the
matching, smaller value.

**How accurate is it?** The calculation assumes flat ground at the height the drone
took off from, so hills, buildings and a wrong field of view all add error. Each
location comes with an **error circle** (the website draws it around the point). When the
camera looks close to the horizon (less than about 5 degrees below it) or the error
would be over 200 m, the location is marked unreliable and shown without an exact
point. Two cases are not covered: when the camera is panned sideways (turned left or
right of the drone's nose), locations are wrong, because the calculation uses the
drone's heading for MAVLink drones; and when the height is assumed (a DJI log with only
`altitude`, which may be measured from sea level instead of from take-off), locations
are marked unreliable. The drone's reading has to be within 0.5 s of the video frame (`telemetry.max_age_s`)
for a location to be computed; otherwise the truck stays "Unknown".

**Map page.** It uses OpenStreetMap tiles, which need internet. Offline, the page still
shows the points on a blank background with a notice. To use another tile server, set
`api.map_tile_url` (it must contain `{z}`, `{x}` and `{y}`). OpenStreetMap's tile
servers are for light use only; see their tile usage policy before you leave the map
open all day or share the website widely.

`api.map_tile_attribution` sets the credit line shown on the map when you use another
tile server (default `© OpenStreetMap contributors`). Never put a secret API key in
`api.map_tile_url`: the URL is sent to every browser that opens the website and is
saved in the run record.

## Dashboard

The website in `dashboard/` (React + TypeScript, built with Vite) shows the live
video, the detected trucks and the engine status. The engine serves the built copy
from `dashboard/dist/` at `/`. It checks for the build on every page load, so a build
made while the engine runs shows up when you reload the page (no restart). Needs npm
and Node.js 22.13 or newer on the 22 line, Node 24, or Node 26 and newer (tested with
Node 24.13).

Build:

```
cd dashboard
npm ci
npm run build
```

Work on the dashboard with live reload: start the engine as usual (port 8000), then
run `npm run dev` in `dashboard/` and open the URL it prints. The dev server forwards
`/api`, `/media`, `/stream.mjpg` and `/ws` to the engine at `127.0.0.1:8000`.
`npm test` runs the tests and `npm run typecheck` checks the types.

Token mode (`api.auth.mode: token`): the dashboard asks for the token once and saves
it in the browser. Images, clips, the video stream and the live connection cannot
send headers, so their URLs carry `?token=...`, which can show up in access logs,
including the engine's own log in `data/logs/`. Use token mode on trusted networks
only.
