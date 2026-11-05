# sky-eye

Drone truck-detection engine: reads a video source, detects and tracks trucks
with RF-DETR, saves a snapshot, crop and short clip per truck, and serves it all
over a small HTTP API with a live MJPEG stream.

## Quick start

1. Install [uv](https://docs.astral.sh/uv/) and run `uv sync`.
2. Copy the example config: `cp config.example.yaml config.yaml`
3. Put a drone clip at `samples/drone.mp4` (or change `source.path` in `config.yaml`).
4. Optional: check the setup without starting anything: `uv run python -m engine --config config.yaml --check`
5. Run: `uv run python -m engine --config config.yaml`
6. Open the printed `Dashboard: http://<ip>:8000/` URL, then `/stream.mjpg` for the
   live annotated video, `/api/trucks` for detected trucks and `/api/status` for FPS.

Evidence is written to `data/media/<date>/` (`_full.jpg`, `_crop.jpg`, `.mp4` per
truck); logs to `data/logs/sky-eye.log`. Press Ctrl+C to stop. For a file source the
API keeps serving after the video ends so you can browse the trucks.

To reach the API from a phone on the same Wi-Fi, allow Python through the Windows
firewall on **Private** networks only. Set `api.auth.mode: token` and
`SKYEYE_API_TOKEN` (in the environment or a `.env` next to `config.yaml`) to require a token.

Exit code 2 with a short message means a config or detector problem (bad value,
missing weights, no CUDA).
