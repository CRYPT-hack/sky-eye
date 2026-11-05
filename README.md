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
4. Put a drone clip at `samples/drone.mp4` (or change `source.path` in `config.yaml`).
5. Optional: check the setup without starting anything (also downloads the weights):
   `uv run python -m engine --config config.yaml --check`
6. Run: `uv run python -m engine --config config.yaml`
7. Open the printed `Dashboard: http://<ip>:8000/` URL. Until the dashboard (Plan 2)
   lands, `/` only returns a small JSON document; use `/stream.mjpg` for the live
   annotated video, `/api/trucks` for detected trucks and `/api/status` for FPS.

Evidence is written to `data/media/<date>/` (`_full.jpg`, `_crop.jpg`, `.mp4` per
truck); logs (including the HTTP server's) to `data/logs/sky-eye.log`. Press Ctrl+C
to stop; clips that have not started encoding are dropped, so the stop is quick. For
a file source the API keeps serving after the video ends so you can browse the trucks.

To reach the API from a phone on the same Wi-Fi, allow Python through the Windows
firewall on **Private** networks only. Set `api.auth.mode: token` and
`SKYEYE_API_TOKEN` (in the environment or a `.env` next to `config.yaml`) to require a token.

Exit code 2 with a short message means a startup problem (bad config value, port
already in use, missing weights, no CUDA). Exit code 1 means the engine did not stop
within 5 s and was ended forcibly.
