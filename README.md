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
to stop; clips that are not finished yet are dropped, so the stop is quick. For
a file source the API keeps serving after the video ends so you can browse the trucks.

To reach the API from a phone on the same Wi-Fi, allow Python through the Windows
firewall on **Private** networks only. Set `api.auth.mode: token` and
`SKYEYE_API_TOKEN` (in the environment or a `.env` next to `config.yaml`) to require a token.

Exit code 2 with a short message means a startup problem (bad config value, port
already in use, missing weights, no CUDA). Exit code 1 means the engine did not stop
within 5 s and was ended forcibly.

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
