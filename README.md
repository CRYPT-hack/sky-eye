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
