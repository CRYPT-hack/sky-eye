# sky-eye — Phase 1 Design Spec

- **Date:** 2026-11-05
- **Status:** Draft, awaiting owner review
- **Repo:** `CRYPT-hack/sky-eye` (private, Apache-2.0)

---

## 1. Goal

Watch a live, cluttered, low-resolution **drone video feed** and **highlight every truck** in it, ignoring everything else. Each truck gets saved as evidence (photo, short clip, time, ground location) and triggers an alert, and everything is visible on a website dashboard.

### What the owner decided

| Topic | Decision |
|---|---|
| Detector type | Closed-set (fixed class list), not open-vocabulary |
| Target class | `truck`: every kind (open-top, closed, tanker, tipper, trailer, car carrier…) merged into **one** class |
| Camera | Drone (aerial view). Must work with **any drone brand**, no specific model owned yet |
| Where AI runs | **Ground laptop** (NVIDIA RTX 4050 Laptop GPU, 6 GB VRAM). Drone streams video down |
| Outputs (all in Phase 1) | Boxes on live video · snapshot + clip + timestamp per truck · alerts (laptop + phone) · map location of each truck · web dashboard |
| Approach | **A**: custom Python app, RF-DETR detector (Apache-2.0). YOLO and Frigate/Home Assistant supported as optional integrations |
| Website | **Local dashboard now** (laptop serves it, open on phone over same Wi-Fi). Built so it can go online later |

### Assumptions (owner may correct)

- One drone feed at a time in Phase 1.
- Drone flies at roughly 30–150 m altitude, camera looking down or at an angle (like VisDrone / UAVDT footage).
- Phase 1 is developed and tested with **recorded drone videos** first. A real drone is plugged in when available.
- Development OS is Windows 11. Code stays cross-platform (Linux needed later for Jetson).

---

## 2. Scope

### In Phase 1

1. Video input from file, webcam/HDMI capture card, network stream (RTSP/RTMP/SRT/UDP), optional go2rtc relay.
2. Truck detection with RF-DETR, **fine-tuned on aerial datasets**, with optional SAHI tiling for tiny trucks.
3. Tracking (stable ID per truck), so each truck is saved, alerted and counted once.
4. Evidence: best snapshot, 8-second clip (3 s before + 5 s after first sighting), timestamps, stored in SQLite + media folder.
5. Alerts: laptop sound + desktop notification, Telegram message with photo, MQTT event (for Home Assistant / Frigate / Node-RED).
6. Ground geolocation of each truck from drone telemetry (MAVLink live; DJI `.SRT` files for recorded flights). "Location unknown" when no telemetry.
7. Local web dashboard: live view with boxes, truck list/gallery, map, counts, system status.
8. Training scripts: dataset download + conversion, fine-tune, evaluate, export.
9. Optional detector backends: YOLO (Ultralytics, off by default because of AGPL), generic ONNX.

### Later phases (recorded so they are not forgotten)

| Later item | How Phase 1 prepares for it |
|---|---|
| **Online dashboard** reachable from anywhere, with login | Frontend talks to backend **only through a documented REST + WebSocket API**. An auth layer slot exists (`auth.mode: none` now). Frontend is a separate static build that can deploy to Vercel later |
| **Public project / landing page** | Separate page in the same frontend project later. No backend dependency |
| **Onboard inference on the drone** (Jetson Orin) | Detector interface + ONNX/TensorRT export already exist. Pipeline has no Windows-only code in its core |
| **Other vehicle classes** (car, bus, motorcycle…) | Class list lives in config; dataset converter already keeps the original subclasses in metadata |
| **Satellite images** | `VideoSource` interface can be implemented by an image-tile source. SAHI tiling already exists. Datasets: xView, DOTA |
| **Live DJI telemetry** | `Telemetry` interface. Phase 1 reads DJI data only from recorded `.SRT` files; live needs a DJI Mobile SDK app |
| **WebRTC video** (low-latency over the internet) | Live view is a swappable component; Phase 1 uses MJPEG |

### Not doing

- Open-vocabulary / "find anything by text" detection.
- Multiple simultaneous drone feeds.
- Controlling the drone (no flight commands are ever sent).
- Identifying number plates, drivers or people.

---

## 3. Architecture

One Python application (the **"engine"**) plus one web frontend (the **"dashboard"**).

```
sky-eye/
├─ engine/                   Python package (the brain)
│  ├─ sources/               VideoSource: file · capture (webcam/HDMI) · stream · go2rtc
│  ├─ detectors/             Detector: rfdetr (default) · yolo (optional) · onnx
│  │   └─ sahi.py            tiling wrapper, works on any Detector
│  ├─ tracking/              Tracker: ByteTrack (via Roboflow `trackers`)
│  ├─ telemetry/             Telemetry: mavlink · dji_srt · none
│  ├─ geo/                   Geolocator: pixel + drone pose → lat/lon
│  ├─ events/                EventManager + Evidence (snapshot, clip ring buffer)
│  ├─ storage/               SQLite repository + media folder
│  ├─ alerts/                Notifier: desktop · telegram · mqtt
│  ├─ api/                   FastAPI: REST, WebSocket, MJPEG, serves dashboard build
│  ├─ pipeline.py            wires everything, runs the threads
│  ├─ config.py              loads + validates config.yaml (pydantic)
│  └─ __main__.py            `python -m engine --config config.yaml`
├─ dashboard/                React + Vite + TypeScript web app
├─ training/                 dataset prep, fine-tune, eval, export scripts
├─ tests/
├─ config.example.yaml
├─ .env.example              secrets (Telegram token, MQTT password)
└─ docs/
```

### Interfaces (the contracts between blocks)

```python
@dataclass
class Frame:
    image: np.ndarray        # BGR, HxWx3
    ts: float                # seconds, monotonic clock of the source
    wall_time: datetime      # UTC
    index: int

class VideoSource(Protocol):
    def open(self) -> None: ...
    def read(self) -> Frame | None    # None = no frame right now
    def close(self) -> None: ...

@dataclass
class Detection:
    box: tuple[float, float, float, float]   # x1, y1, x2, y2 in pixels
    score: float
    class_name: str                          # "truck"

class Detector(Protocol):
    def detect(self, image: np.ndarray) -> list[Detection]: ...

@dataclass
class Track:
    track_id: int
    detection: Detection
    age_frames: int

class Tracker(Protocol):
    def update(self, detections: list[Detection], frame: Frame) -> list[Track]: ...

@dataclass
class DronePose:
    ts: float
    lat: float; lon: float
    alt_agl_m: float                          # height above ground
    yaw_deg: float                            # heading
    cam_pitch_deg: float                      # gimbal tilt, -90 = straight down
    cam_roll_deg: float

class Telemetry(Protocol):
    def pose_at(self, ts: float) -> DronePose | None: ...

class Notifier(Protocol):
    def send(self, event: TruckEvent) -> None: ...   # must never raise into the pipeline
```

Each block is created from config by a small factory (`build_source(cfg)`, `build_detector(cfg)`…), so swapping an implementation is a config change.

---

## 4. Data flow and threading

```
[capture thread] ─frames→ (latest-frame slot, size 1) ─→ [inference thread]
                                                            detector → tracker
                                                            → EventManager ─┬→ storage
                         [telemetry thread] → pose buffer ──→ geo           ├→ alert queue → [alert thread]
                                                                            └→ WebSocket broadcast
[capture thread] ─frames→ ring buffer (last 3 s, for clips)
[inference thread] ─annotated frame→ MJPEG broadcaster ─→ dashboard
[api: uvicorn / asyncio]
```

- **Live beats complete.** The capture thread always overwrites a single "latest frame" slot. If the GPU is slower than the camera, frames are skipped instead of queued, so lag never builds up.
- Alerts run in their own thread with a queue, so a slow Telegram call never stalls detection.
- Clip writing runs in a worker thread (it waits 5 s for "after" frames).

---

## 5. Components

### 5.1 Video sources

| Type | Library | Notes |
|---|---|---|
| `file` | OpenCV | Optional `realtime: true` paces playback to the file's FPS so tests feel live |
| `capture` | OpenCV (`CAP_DSHOW` on Windows) | Webcam or USB HDMI capture card (device index) |
| `stream` | PyAV (FFmpeg) | `rtsp://`, `rtmp://`, `srt://`, `udp://`. Low-latency flags. Auto-reconnect with backoff (1 s → 30 s max) |
| `go2rtc` | stream via local go2rtc | For drone streams that need protocol conversion |

### 5.2 Detector

- **Default:** RF-DETR (Roboflow, Apache-2.0). Start with the **Small** size. Move to Medium only if it still reaches ≥ 15 FPS on the RTX 4050 and improves truck AP50.
- **Fine-tuning data**, all trucks merged into one class `truck`:
  - **VisDrone-DET** (`truck` class, low-altitude drone, cluttered scenes). Primary.
  - **UAVDT** (`truck` class, drone highway traffic).
  - Other vehicle classes in these datasets are kept as **negative examples** (labelled but not "truck"), so the model learns truck ≠ car/bus/van.
  - DOTA is not used in Phase 1 (much higher altitude) and is reserved for the satellite phase.
- **Training location:** RTX 4050 for small runs. Kaggle / Google Colab free GPUs if 6 GB VRAM is too small.
- **Input size:** 640–1280 px, chosen by benchmark (bigger = finds smaller trucks, slower).
- **Export:** ONNX, with optional TensorRT FP16 for speed.
- **SAHI tiling:** optional wrapper. Splits the frame into overlapping tiles, detects per tile, merges boxes. Off by default for live use. Turned on when trucks are tiny and FPS budget allows.
- **Optional backends:**
  - `yolo`: Ultralytics, installed only via `pip install sky-eye[yolo]`. Logs a one-time AGPL license warning.
  - `onnx`: any exported model + class map.

### 5.3 Tracker

ByteTrack via Roboflow's `trackers` package (Apache-2.0; supervision 0.31 no longer ships a tracker), fed each frame's capture timestamp so dropped frames in realtime mode do not split a track. A track becomes a **confirmed truck** after it appears in ≥3 of the last 5 processed frames, which filters one-frame false alarms. Lost tracks are kept for 2 s so short occlusions (bridges, trees) don't create a new ID.

### 5.4 Telemetry and geolocation

- `mavlink` (pymavlink): reads `GLOBAL_POSITION_INT` (lat, lon, relative altitude), `ATTITUDE` (yaw), and gimbal attitude (`GIMBAL_DEVICE_ATTITUDE_STATUS`, falling back to `MOUNT_STATUS`). Works with PX4 / ArduPilot over UDP or serial.
- `dji_srt`: parses the `.SRT` subtitle file that many DJI drones save next to the video (per-frame lat, lon, altitude, sometimes gimbal angles). Recorded flights only.
- `none`: every event gets `location: unknown`.
- **Geolocation method:** pinhole camera model with configured horizontal FOV. A ray from the camera through the box's bottom-centre pixel, rotated by gimbal + drone yaw, is intersected with a **flat ground plane** at the drone's height above ground. The result is converted to lat/lon.
- **Accuracy:** each location carries an estimated error radius (grows with altitude and low camera tilt). Locations with the camera tilted above the horizon, or error over 200 m, are marked unreliable and are not drawn as exact points.
- Pose and frame are matched by timestamp (nearest pose within 0.5 s, otherwise unknown).

### 5.5 Events and evidence

- **New confirmed truck →** `TruckEvent` created:
  - **snapshot:** the frame where the truck had its highest score during its first 2 s, cropped + full-frame copy (JPEG).
  - **clip:** 3 s before + 5 s after first confirmation, H.264 MP4 so browsers can play it, from the capture ring buffer.
  - timestamps (first seen, last seen), best score, track ID, location + error radius, source name.
- **Track ends →** event updated with `last_seen`.
- **Storage:** SQLite (`data/sky-eye.db`) + `data/media/YYYY-MM-DD/`. Tables:
  - `runs` (id, started_at, source, config snapshot)
  - `trucks` (id, run_id, track_id, first_seen, last_seen, best_score, snapshot_path, crop_path, clip_path, lat, lon, geo_error_m, geo_status)
- **Retention:** configurable max disk use (default 20 GB). Oldest clips are deleted first, DB rows are kept.

### 5.6 Alerts

| Channel | How | Default |
|---|---|---|
| Desktop | sound + OS notification (plyer) | on |
| Telegram | bot `sendPhoto` with crop, time, map link | off until token set in `.env` |
| MQTT | JSON event to `sky-eye/events` (paho-mqtt) | off |

- **Cooldown:** at most one alert per channel every `cooldown_s` seconds (default 10). Extra trucks in that window are grouped ("3 new trucks").
- A failing channel logs an error and retries 3× with backoff. It never stops detection.

### 5.7 Engine API (the contract the dashboard uses)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/status` | FPS, GPU, source state, telemetry state, uptime, current/total truck counts |
| GET | `/api/trucks?from=&to=&limit=&offset=` | List of saved trucks |
| GET | `/api/trucks/{id}` | One truck with media URLs |
| GET | `/media/...` | Snapshot / crop / clip files |
| GET | `/stream.mjpg` | Live annotated video (MJPEG) |
| WS | `/ws` | Live push: `truck.new`, `truck.updated`, `status` messages |
| GET / PUT | `/api/config/runtime` | Change runtime settings: confidence threshold, SAHI on/off, alert channels on/off |

- The API is versioned in the JSON (`"api": 1`), so a future online dashboard can rely on it.
- `auth.mode`: `none` (Phase 1, LAN only) or `token` (shared secret header). Full login comes with the online phase.
- Binds to `0.0.0.0:8000` so phones on the same Wi-Fi can open it. A startup log prints the LAN URL.

### 5.8 Dashboard (website)

React + Vite + TypeScript. Built to static files, served by the engine at `/`. Pages:

1. **Live:** video with boxes, truck count now + total this run, latest-truck strip, FPS and connection status.
2. **Trucks:** gallery/table of saved trucks with filter by time, click → snapshot, clip player, details, location.
3. **Map:** Leaflet + OpenStreetMap. Truck points (colour = age), drone position + track when telemetry exists, error circles.
4. **Status / Settings:** source, model, GPU, runtime toggles from `/api/config/runtime`.

Mobile-friendly layout (phone over Wi-Fi is a main use). New trucks pop in live via WebSocket, with an optional browser sound.

### 5.9 Configuration

`config.yaml` (validated by pydantic, clear error messages on bad values). Secrets stay in `.env`, never in YAML or git.

```yaml
source:    { type: file, path: samples/highway.mp4, realtime: true }
detector:  { type: rfdetr, weights: models/sky-eye-truck.pth, input_size: 960, confidence: 0.4, device: cuda }
sahi:      { enabled: false, tile: 640, overlap: 0.2 }
tracker:   { confirm_hits: 3, confirm_window: 5, lost_timeout_s: 2 }
telemetry: { type: none }            # mavlink | dji_srt | none
camera:    { hfov_deg: 82.0 }
evidence:  { clip_before_s: 3, clip_after_s: 5, max_disk_gb: 20 }
alerts:    { cooldown_s: 10, desktop: true, telegram: false, mqtt: false }
api:       { host: 0.0.0.0, port: 8000, auth: { mode: none } }
```

---

## 6. Error handling

| Failure | Behaviour |
|---|---|
| Stream drops | Status → `reconnecting`, auto-retry with backoff, dashboard shows a banner. Detection resumes automatically |
| File ends | Run finishes cleanly, or loops if `loop: true` |
| CUDA unavailable / out of memory | Clear startup error naming the cause. Option `device: cpu` for slow fallback. OOM at runtime → log, skip frame, suggest smaller `input_size` |
| Model weights missing | Startup error with the exact download/train command |
| Telemetry missing or stale | Events saved with `geo_status: unknown`. Detection unaffected |
| Alert channel fails | Logged, retried, never blocks detection |
| Disk near limit | Oldest clips deleted, warning on dashboard |
| Bad config | Refuses to start, prints which field is wrong and why |

Logs: structured logs to console + rotating file `data/logs/`.

---

## 7. Testing

- **Unit tests (pytest)** per block, using fakes:
  - geolocation math against hand-computed cases (straight-down camera, 45° tilt, yaw rotation)
  - DJI `.SRT` parser and MAVLink message handling with recorded samples
  - tracker confirmation / lost-timeout rules
  - event creation, cooldown and grouping in alerts
  - config validation
- **Integration test:** full pipeline on a short sample clip with a **fake detector** (scripted boxes). Asserts: correct number of trucks saved, each once, clip + snapshot files exist, WebSocket messages sent.
- **Model evaluation script:** truck AP50 / recall on VisDrone-DET val + UAVDT test split, comparing COCO-pretrained vs fine-tuned.
- **Performance benchmark:** FPS + latency on the RTX 4050 for each input size, SAHI on/off.
- **Dashboard:** component tests for the truck list and live-update handling. A manual check on a phone over Wi-Fi.

---

## 8. Success criteria (Phase 1 done when…)

1. `python -m engine --config config.yaml` runs end-to-end on a recorded drone clip and on at least one live input (webcam/HDMI capture or RTSP stream).
2. **≥ 15 FPS** processed on the RTX 4050 at the default settings (SAHI off).
3. **Fine-tuned model beats the COCO-pretrained model** on truck AP50 on the VisDrone-DET val split, and the numbers are recorded in `docs/results.md`.
4. Each truck in a test clip is saved **once** (duplicate events ≤ 10% of trucks in the clip).
5. Dashboard works on a phone over the same Wi-Fi: live view, truck list with clip playback, map.
6. Desktop + Telegram alerts arrive within 3 s of a truck being confirmed.
7. With a DJI `.SRT` sample or a MAVLink simulator (PX4/ArduPilot SITL), trucks appear on the map with an error radius.
8. All unit and integration tests pass.

---

## 9. Tech stack and licenses

| Part | Choice | License |
|---|---|---|
| Language / env | Python **3.12** via `uv` (system has 3.14; 3.12 is pinned for PyTorch/CUDA compatibility) | — |
| Deep learning | PyTorch (CUDA) | BSD |
| Detector | RF-DETR | Apache-2.0 |
| Tracking | Roboflow `trackers` (ByteTrack) | Apache-2.0 |
| Drawing, detection types | supervision | MIT |
| Small objects | SAHI | MIT |
| Video | OpenCV, PyAV (FFmpeg) | Apache-2.0 / BSD (FFmpeg LGPL; see note) |
| Inference speed-up | ONNX Runtime, TensorRT (optional) | MIT / NVIDIA |
| Web server | FastAPI + uvicorn | MIT / BSD |
| Database | SQLite via Python's built-in `sqlite3` | Public domain |
| Telemetry | pymavlink | LGPL-3.0 (used as an unmodified library) |
| Alerts | plyer (desktop), httpx calls to the Telegram Bot API, paho-mqtt | MIT / BSD / EPL-2.0 |
| Dashboard | React, Vite, TypeScript, Leaflet | MIT / BSD-2 |
| Map tiles | OpenStreetMap | ODbL (attribution shown) |
| Optional | Ultralytics YOLO | **AGPL-3.0**, opt-in extra only |

Exact versions are pinned in the implementation plan after checking the latest releases.

**Note on x264:** the PyAV wheel bundles an FFmpeg built with libx264, which is **GPL-2.0+**; the clips are encoded with it. That is fine for running sky-eye from source; it matters only if we ever redistribute a bundled binary (e.g. a PyInstaller build or an installer), which would then have to meet the GPL, or switch to an LGPL-only FFmpeg and another H.264 encoder (the engine already falls back to `h264_nvenc`, `libopenh264` or `h264_mf`).

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Trucks too small / blurry in low-res feed | Larger input size, SAHI, fine-tuning on aerial data. Measured in the eval script |
| 6 GB VRAM too small for training | Smaller RF-DETR size, small batch + gradient accumulation, or Kaggle/Colab |
| "Any drone" video links vary a lot | Three input paths (HDMI capture, network stream, go2rtc relay) cover most drones |
| Geolocation is rough (flat-ground assumption, telemetry lag) | Show error radius, mark unreliable points, never present location as exact |
| Truck vs van/bus confusion | Other vehicles kept as negatives in training. Confidence threshold tunable live |
| Privacy / legal: aerial surveillance | Phase 1 is local only. No faces or plates are processed. Owner is responsible for local drone and privacy laws |

---

## 11. Build order (feeds the implementation plan)

1. Project skeleton: uv env, config, interfaces, tests setup.
2. File source → pretrained detector (COCO "truck") → boxes on screen. **First visible result.**
3. Tracker + events + storage + evidence.
4. Engine API + dashboard (Live, Trucks pages).
5. Alerts (desktop, Telegram, MQTT).
6. Training pipeline: VisDrone + UAVDT → fine-tuned model → eval → swap in.
7. Live sources (capture, stream, go2rtc) + reconnect.
8. Telemetry + geolocation + Map page.
9. SAHI, ONNX/TensorRT export, YOLO/ONNX optional backends.
10. Benchmark, success-criteria check, `docs/results.md`.
