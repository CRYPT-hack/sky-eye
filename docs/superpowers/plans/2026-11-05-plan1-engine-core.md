# sky-eye Plan 1 — Engine Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python engine that reads a drone video, finds trucks with RF-DETR, gives each truck one ID, saves each truck once (snapshot, crop, 8 s clip, timestamps) in SQLite, and serves live annotated video + truck data over a local HTTP/WebSocket API.

**Architecture:** One Python package `engine/` built from small blocks behind Protocols (`VideoSource`, `Detector`, `Tracker`), wired by a `Pipeline` that runs a capture thread and an inference thread. An `EventManager` turns confirmed tracks into stored `TruckRecord`s and publishes messages on an in-process `EventBus`. A FastAPI app exposes the spec's API (`/api/*`, `/media`, `/stream.mjpg`, `/ws`).

**Tech Stack:** Python 3.12 (uv), PyTorch CUDA, rfdetr 1.11.x, supervision 0.31.x (ByteTrack, annotators), OpenCV, PyAV, FastAPI + uvicorn, pydantic 2, SQLite (`sqlite3`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-05-sky-eye-phase1-design.md`

### Where this plan sits

The spec has several independent subsystems, so Phase 1 is split into six plans. Each one ends with working, testable software:

| Plan | Delivers | Spec sections |
|---|---|---|
| **1 — Engine core (this plan)** | file/capture input → RF-DETR (COCO-pretrained "truck") → tracking → evidence + SQLite → HTTP/WS/MJPEG API | 3, 4, 5.1 (file/capture), 5.2 (pretrained), 5.3, 5.5, 5.7, 5.9, 6 |
| 2 — Dashboard website | React + Vite + TS: Live, Trucks, Status/Settings pages, served at `/` | 5.8 |
| 3 — Alerts + live sources | desktop/Telegram/MQTT alerts with cooldown; RTSP/RTMP/SRT/UDP + go2rtc sources with reconnect | 5.1, 5.6 |
| 4 — Training | VisDrone + UAVDT conversion, fine-tune RF-DETR, eval script, `docs/results.md` | 5.2, 7, 8.3 |
| 5 — Telemetry + geolocation + Map | MAVLink, DJI `.SRT`, flat-ground geolocation, Map page | 5.4, 5.8 |
| 6 — Speed + options | SAHI wrapper, ONNX/TensorRT export, YOLO/ONNX backends, benchmark, success-criteria check | 5.2, 8 |

## Global Constraints

- Python **3.12** project interpreter (system Python is 3.14 and must not be used for the project venv).
- Default install contains **no AGPL dependency**. Ultralytics is never added in this plan.
- Secrets live in `.env` only (`SKYEYE_API_TOKEN` in this plan). `.env`, `data/`, `models/`, `samples/` are git-ignored.
- API binds `0.0.0.0:8000` by default and every JSON body/message carries `"api": 1`.
- **Live beats complete:** with `source.realtime: true` the capture thread overwrites a single latest-frame slot (frames are dropped, never queued). With `realtime: false` (file processing / tests) every frame is processed.
- A track becomes a confirmed truck after ≥ `confirm_hits` (3) hits in the last `confirm_window` (5) processed frames; a track is ended after `lost_timeout_s` (2.0 s) unseen.
- Clip = `clip_before_s` (3) + `clip_after_s` (5) seconds, H.264 MP4. Snapshot = best-scoring frame within `snapshot_window_s` (2) of first sighting.
- Storage: `data/sky-eye.db` + `data/media/YYYY-MM-DD/`; retention default `max_disk_gb: 20`, deleting oldest `.mp4` first, DB rows kept.
- Paths are handled with `pathlib`; the repo lives under a path with spaces (`C:\Users\Shobhit Rai\Desktop\auto detection\sky-eye`).
- Code must stay cross-platform (no Windows-only imports outside `sources/capture.py`'s backend choice).

## Review Focus

1. **Paths with spaces** (`Shobhit Rai`, `auto detection`) in data dir, video path and media URLs → everything still loads and `/media/...` URLs resolve. Test: Task 6 `test_repo_in_dir_with_spaces`, Task 10 `test_media_url_served_from_dir_with_spaces`.
2. **Truck already visible at video start or still visible at video end** → event is still saved; clip is written with the frames available (shorter than 8 s), never dropped. Test: Task 7 `test_clip_shorter_when_video_ends`, Task 9 integration asserts clip exists for a truck present until the last frame.
3. **Video with missing/zero FPS metadata** (common for capture cards and some files) → fall back to 30 FPS for pacing, ring buffer and clip timing. Test: Task 3 `test_fps_fallback_when_zero`.
4. **Dashboard client disconnecting abruptly / slow WebSocket client** → pipeline never blocks or crashes; slow subscribers drop oldest messages. Test: Task 8 `test_bus_slow_subscriber_drops_oldest`, Task 10 `test_ws_disconnect_does_not_break_bus`.
5. **Same truck briefly hidden (< 2 s) by a bridge/tree** → keeps its ID, no duplicate event; hidden > 2 s → may get a new ID (accepted). Test: Task 5 `test_short_gap_keeps_id`, `test_long_gap_ends_track`.

---

### Task 1: Project skeleton and environment

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.python-version`, `engine/__init__.py`, `engine/__main__.py` (stub), `tests/__init__.py`, `tests/test_smoke.py`, `.env.example`

**Interfaces:**
- Produces: importable package `engine` with `engine.__version__ = "0.1.0"`; `uv run pytest` works; marker `gpu` registered (tests needing CUDA/model downloads).

- [ ] **Step 1: Install uv and Python 3.12**

Run: `python -m pip install --user uv` then `uv python install 3.12`
Expected: `uv --version` prints a version; `uv python find 3.12` prints a path.

- [ ] **Step 2: Write `pyproject.toml`**

- `requires-python = ">=3.12,<3.13"`, project name `sky-eye`, version `0.1.0`, license `Apache-2.0`.
- Dependencies: `rfdetr~=1.11.2`, `supervision~=0.31.4`, `opencv-python>=4.10,<6`, `av~=19.0`, `fastapi~=0.162`, `uvicorn[standard]~=0.54`, `pydantic~=2.14`, `pyyaml~=6.0`, `numpy>=2.0`, `torch` (CUDA build).
- Dev group: `pytest~=9.2`, `httpx~=0.28`.
- CUDA torch via uv: an explicit index `pytorch-cu130` = `https://download.pytorch.org/whl/cu130` and `[tool.uv.sources] torch = { index = "pytorch-cu130" }`. If uv reports no matching torch wheel on cu130, switch the URL to `cu128`.
- `[tool.pytest.ini_options]`: `markers = ["gpu: needs CUDA and model download"]`, `addopts = "-m 'not gpu'"`.
- `.gitignore`: `.venv/`, `__pycache__/`, `.env`, `data/`, `models/`, `samples/`, `*.pth`, `*.onnx`, `dashboard/node_modules/`, `dashboard/dist/`.
- `.env.example`: `SKYEYE_API_TOKEN=change-me`.

- [ ] **Step 3: Write the smoke test** `tests/test_smoke.py`

```python
import engine

def test_version():
    assert engine.__version__ == "0.1.0"

def test_torch_imports():
    import torch
    assert torch.__version__
```

Plus a `@pytest.mark.gpu` test `test_cuda_available` asserting `torch.cuda.is_available()` and that the device name contains `"4050"`.

- [ ] **Step 4: Sync and run**

Run: `uv sync` then `uv run pytest -v` and `uv run pytest -m gpu -v`
Expected: 2 passed (default run); 1 passed (gpu run). If `test_cuda_available` fails, fix the torch index before continuing.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .python-version .env.example engine tests
git commit -m "chore: project skeleton with uv, Python 3.12, CUDA torch"
```

---

### Task 2: Configuration

**Files:**
- Create: `engine/config.py`, `config.example.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces (all pydantic models with `model_config = ConfigDict(extra="forbid")`, every field defaulted):
  - `SourceConfig(type: Literal["file","capture"]="file", path: Path | None = None, device: int = 0, realtime: bool = True, loop: bool = False)`
  - `DetectorConfig(type: Literal["rfdetr"]="rfdetr", size: Literal["nano","small","medium"]="small", weights: Path | None = None, input_size: int = 512, confidence: float = 0.4, device: Literal["cuda","cpu"]="cuda", classes: list[str] = ["truck"])` — `None` weights = COCO-pretrained.
  - `TrackerConfig(confirm_hits: int = 3, confirm_window: int = 5, lost_timeout_s: float = 2.0)`
  - `EvidenceConfig(data_dir: Path = Path("data"), clip_before_s: float = 3, clip_after_s: float = 5, snapshot_window_s: float = 2, max_disk_gb: float = 20)`
  - `AuthConfig(mode: Literal["none","token"]="none")`, `ApiConfig(host: str = "0.0.0.0", port: int = 8000, auth: AuthConfig, stream_fps: int = 15, stream_max_width: int = 1280)`
  - `AppConfig(source, detector, tracker, evidence, api)` (sahi/telemetry/alerts/camera sections arrive in Plans 3, 5, 6)
  - `class ConfigError(Exception)`
  - `load_config(path: Path) -> AppConfig`

- [ ] **Step 1: Write failing tests**

```python
def test_defaults_match_spec(tmp_path):
    p = tmp_path / "c.yaml"; p.write_text("{}")
    cfg = load_config(p)
    assert cfg.detector.confidence == 0.4
    assert cfg.tracker.confirm_hits == 3 and cfg.tracker.confirm_window == 5
    assert cfg.tracker.lost_timeout_s == 2.0
    assert cfg.evidence.clip_before_s == 3 and cfg.evidence.clip_after_s == 5
    assert cfg.api.host == "0.0.0.0" and cfg.api.port == 8000

def test_bad_value_names_field(tmp_path):      # confidence: 1.5
    with pytest.raises(ConfigError, match=r"detector\.confidence"): ...

def test_unknown_key_rejected(tmp_path):        # detector: {colour: red}
    with pytest.raises(ConfigError, match=r"detector\.colour"): ...

def test_file_source_requires_existing_path(tmp_path):
    with pytest.raises(ConfigError, match=r"source"): ...   # type: file, path: missing.mp4

def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"): load_config(tmp_path / "nope.yaml")

def test_token_mode_requires_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SKYEYE_API_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="SKYEYE_API_TOKEN"): ...  # api.auth.mode: token
```

- [ ] **Step 2: Run** `uv run pytest tests/test_config.py -v` — Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement `engine/config.py`**

Validators: `0 < confidence < 1`; `input_size > 0`; `confirm_hits <= confirm_window`; file source path must exist; token mode requires env var `SKYEYE_API_TOKEN` non-empty. `load_config` reads YAML (empty file = `{}`), converts `pydantic.ValidationError` into one `ConfigError` whose message has one line per error formatted `"<dotted.loc>: <msg>"`. Relative `source.path`, `detector.weights`, `evidence.data_dir` resolve against the config file's folder. Write `config.example.yaml` with every section and the defaults above, `source.path: samples/drone.mp4`.

- [ ] **Step 4: Run** `uv run pytest tests/test_config.py -v` — Expected: 6 passed.

- [ ] **Step 5: Commit** — `git add engine/config.py config.example.yaml tests/test_config.py && git commit -m "feat: validated YAML config"`

---

### Task 3: Core types and video sources

**Files:**
- Create: `engine/core.py`, `engine/sources/__init__.py`, `engine/sources/file.py`, `engine/sources/capture.py`, `tests/conftest.py`
- Test: `tests/test_sources.py`

**Interfaces:**
- Produces in `engine/core.py`:
  - `@dataclass(frozen=True) Frame(image: np.ndarray, ts: float, wall_time: datetime, index: int)` — BGR, `ts` seconds from source start.
  - `@dataclass(frozen=True) Detection(box: tuple[float,float,float,float], score: float, class_name: str)`
  - `@dataclass(frozen=True) Track(track_id: int, detection: Detection)`
  - `@dataclass(frozen=True) TrackerResult(confirmed: list[Track], ended_ids: list[int])`
  - Protocols `VideoSource` (`open()`, `read() -> Frame | None`, `close()`, property `fps: float`, property `ended: bool`), `Detector` (`detect(image: np.ndarray) -> list[Detection]`), `Tracker` (`update(dets: list[Detection], frame: Frame) -> TrackerResult`).
  - `DEFAULT_FPS = 30.0`
- Produces: `FileSource(path: Path, realtime: bool, loop: bool)`, `CaptureSource(device: int)`, `build_source(cfg: SourceConfig) -> VideoSource` in `engine/sources/__init__.py`.
- Produces in `tests/conftest.py`: fixture-helper `make_video(path: Path, n_frames: int, fps: float = 30, size=(320, 240)) -> Path` writing an mp4 (OpenCV `mp4v`) where frame *i* has a white rectangle at x = 10 + 2*i, plus `fake_frame(index: int, ts: float) -> Frame`.

- [ ] **Step 1: Write failing tests**

```python
def test_file_source_reads_all_frames(tmp_path):
    src = FileSource(make_video(tmp_path / "v.mp4", 45), realtime=False, loop=False)
    src.open(); frames = []
    while (f := src.read()) is not None: frames.append(f)
    assert len(frames) == 45 and src.ended
    assert frames[1].ts == pytest.approx(1 / 30, abs=1e-3)
    assert [f.index for f in frames] == list(range(45))

def test_file_source_loop(tmp_path):            # loop=True: read 60 frames from a 45-frame file, index keeps increasing, ended stays False
def test_fps_fallback_when_zero(tmp_path, monkeypatch):  # patch cv2 CAP_PROP_FPS read to 0 → src.fps == DEFAULT_FPS
def test_realtime_paces_reading(tmp_path):      # realtime=True, 15 frames @30fps take >= 0.4 s
def test_build_source_from_config(tmp_path):    # SourceConfig(type="file", path=...) → FileSource
def test_video_path_with_spaces(tmp_path):      # file under tmp_path / "auto detection" opens and reads
```

- [ ] **Step 2: Run** `uv run pytest tests/test_sources.py -v` — Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement**

`FileSource` uses `cv2.VideoCapture(str(path))`; `ts = index / fps`; `fps` falls back to `DEFAULT_FPS` when the property is `<= 0` or NaN; `realtime=True` sleeps so frame *i* is returned no earlier than `start + ts`; on end, `loop=True` reopens and continues `index`/`ts`. `CaptureSource` uses `cv2.CAP_DSHOW` on Windows (`sys.platform == "win32"`), default backend elsewhere, `ts` from `time.monotonic() - start`. `read()` returns `None` without raising when a frame is unavailable.

- [ ] **Step 4: Run** `uv run pytest tests/test_sources.py -v` — Expected: 6 passed.

- [ ] **Step 5: Commit** — `git commit -m "feat: core types, file and capture sources"` (add the files above).

---

### Task 4: RF-DETR detector

**Files:**
- Create: `engine/detectors/__init__.py`, `engine/detectors/rfdetr.py`, `engine/detectors/fake.py`
- Test: `tests/test_detectors.py`

**Interfaces:**
- Consumes: `Detection`, `DetectorConfig`.
- Produces:
  - `RFDETRDetector(size: str, weights: Path | None, input_size: int, confidence: float, device: str, classes: list[str])` with `detect(image_bgr) -> list[Detection]` and settable attribute `confidence: float` (runtime changes, read on every call).
  - `FakeDetector(script: Callable[[int], list[Detection]])` — calls `script(call_index)` each `detect`; `call_index` starts at 0.
  - `build_detector(cfg: DetectorConfig) -> Detector`
  - `class DetectorError(Exception)` (bad size/class name, CUDA missing, weights missing).

- [ ] **Step 1: Write failing tests** (unit tests use a stub in place of the rfdetr model; no download)

```python
def test_filters_to_requested_classes(stub_model):
    # stub returns sv.Detections with COCO ids for car(3), truck(8), bus(6), scores .9/.8/.7
    det = make_detector(stub_model, classes=["truck"])
    out = det.detect(np.zeros((240, 320, 3), np.uint8))
    assert [d.class_name for d in out] == ["truck"] and out[0].score == pytest.approx(0.8)

def test_passes_rgb_to_model(stub_model):       # BGR pixel (255,0,0) arrives at model as (0,0,255)
def test_uses_runtime_confidence(stub_model):   # det.confidence = 0.85 → model.predict called with threshold=0.85
def test_unknown_class_rejected():               # classes=["spaceship"] → DetectorError naming it
def test_cuda_missing_error(monkeypatch):        # torch.cuda.is_available → False, device="cuda" → DetectorError mentioning "device: cpu"
def test_missing_weights_error(tmp_path):        # weights=tmp_path/"nope.pth" → DetectorError with the path

@pytest.mark.gpu
def test_real_model_finds_truck_on_sample():
    # downloads RF-DETR Small; image = owner-supplied tests/data/truck.jpg (any photo with a clearly visible truck); pytest.skip if the file is missing
    assert any(d.class_name == "truck" for d in build_detector(DetectorConfig()).detect(img))
```

- [ ] **Step 2: Run** `uv run pytest tests/test_detectors.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

Model class from size: `{"nano": RFDETRNano, "small": RFDETRSmall, "medium": RFDETRMedium}` (from `rfdetr`). Construct with `resolution=input_size` and `pretrain_weights=str(weights)` when weights given; rfdetr raises if the resolution is not a multiple of its block size — re-raise as `DetectorError` naming `detector.input_size`. Class names come from `rfdetr.assets.coco_classes.COCO_CLASSES` (id → name; `8 → "truck"`); invert it once to validate `classes`. Call `model.predict(rgb, threshold=self.confidence, include_source_image=False)`; convert BGR→RGB with `image[..., ::-1]` (contiguous copy). Map `detections.xyxy/confidence/class_id` to `Detection`. Call `model.optimize_for_inference()` once if available, wrapped in try/except with a logged warning.

- [ ] **Step 4: Run** `uv run pytest tests/test_detectors.py -v` then `uv run pytest -m gpu tests/test_detectors.py -v` — Expected: 6 passed; 1 passed.

- [ ] **Step 5: Commit** — `git commit -m "feat: RF-DETR detector with class filter"`

---

### Task 5: Tracker with confirmation and lost timeout

**Files:**
- Create: `engine/tracking/__init__.py`, `engine/tracking/bytetrack.py`
- Test: `tests/test_tracking.py`

**Interfaces:**
- Consumes: `Detection`, `Frame`, `Track`, `TrackerResult`, `TrackerConfig`.
- Produces: `ByteTrackTracker(confirm_hits: int, confirm_window: int, lost_timeout_s: float, fps: float)` implementing `Tracker`; `build_tracker(cfg: TrackerConfig, fps: float) -> Tracker`.

- [ ] **Step 1: Write failing tests** (frames from `fake_frame`, boxes drift 2 px per frame)

```python
def test_confirmed_after_three_hits():
    # same box in frames 0,1 → confirmed == []; frame 2 → one Track
def test_one_frame_blip_never_confirmed():
    # box only in frame 0, then empty frames 1..10 → never in confirmed
def test_three_of_five_with_gaps():
    # hits at frames 0, 2, 4 → confirmed at frame 4
def test_short_gap_keeps_id():
    # visible frames 0-29 (ts step 1/30), hidden 30-74 (1.5 s), visible 75-90 → same track_id, ended_ids empty
def test_long_gap_ends_track():
    # visible 0-29, hidden for 2.1 s → that id appears exactly once in ended_ids
def test_two_trucks_two_ids():
    # two far-apart boxes every frame → two distinct track_ids after frame 2
```

- [ ] **Step 2: Run** `uv run pytest tests/test_tracking.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

Wrap `supervision.ByteTrack(frame_rate=round(fps), lost_track_buffer=ceil(lost_timeout_s * fps), minimum_consecutive_frames=1)`; convert `Detection` lists to `sv.Detections` and back. Keep per-id state: `deque(maxlen=confirm_window)` of hit booleans for every processed frame, `confirmed: bool`, `last_seen_ts`. A track is reported in `confirmed` on each frame it is detected once its hit count ≥ `confirm_hits` (and stays confirmed). An id goes into `ended_ids` exactly once when `frame.ts - last_seen_ts > lost_timeout_s`; only previously confirmed ids are reported as ended.

- [ ] **Step 4: Run** `uv run pytest tests/test_tracking.py -v` — Expected: 6 passed.

- [ ] **Step 5: Commit** — `git commit -m "feat: ByteTrack tracker with confirmation rule"`

---

### Task 6: Storage (SQLite + media + retention)

**Files:**
- Create: `engine/storage/__init__.py`, `engine/storage/repository.py`, `engine/storage/retention.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces:
  - `@dataclass TruckRecord(id: int | None, run_id: int, track_id: int, first_seen: datetime, last_seen: datetime, best_score: float, snapshot_path: str | None = None, crop_path: str | None = None, clip_path: str | None = None, lat: float | None = None, lon: float | None = None, geo_error_m: float | None = None, geo_status: str = "unknown")` — media paths are **relative to `data_dir/media`, forward slashes**; datetimes UTC.
  - `TruckRepository(db_path: Path)`: `create_run(source: str, config_json: str) -> int`, `insert_truck(rec: TruckRecord) -> int`, `update_truck(truck_id: int, **fields) -> None`, `get_truck(truck_id: int) -> TruckRecord | None`, `list_trucks(start: datetime | None = None, end: datetime | None = None, limit: int = 50, offset: int = 0) -> list[TruckRecord]` (newest first), `count_trucks(run_id: int) -> int`, `close()`.
  - `media_dir(data_dir: Path) -> Path`, `day_dir(data_dir: Path, when: datetime) -> Path` (`media/YYYY-MM-DD`, created).
  - `enforce_retention(media_root: Path, max_bytes: int) -> list[Path]` (deleted files).

- [ ] **Step 1: Write failing tests**

```python
def test_insert_and_get_roundtrip(tmp_path)        # all fields equal after get_truck
def test_list_newest_first_with_time_filter(tmp_path)  # 3 trucks at t0,t1,t2; start=t1 → [t2, t1]
def test_update_truck_fields(tmp_path)              # update_truck(id, last_seen=..., clip_path="2026-11-05/a.mp4")
def test_update_unknown_field_rejected(tmp_path)    # update_truck(id, colour="red") → ValueError
def test_threadsafe_inserts(tmp_path)               # 4 threads × 25 inserts → count_trucks == 100
def test_repo_in_dir_with_spaces(tmp_path)          # db under tmp_path/"auto detection"/"data"
def test_retention_deletes_oldest_mp4_first(tmp_path):
    # files: old.mp4 (mtime -3h, 600 B), new.mp4 (600 B), snap.jpg (600 B, oldest); max_bytes=1300
    assert enforce_retention(root, 1300) == [root / "old.mp4"] and (root / "snap.jpg").exists()
```

- [ ] **Step 2: Run** `uv run pytest tests/test_storage.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

Single `sqlite3` connection with `check_same_thread=False`, guarded by a `threading.Lock`; `PRAGMA journal_mode=WAL`. Tables `runs(id, started_at, source, config_json)` and `trucks(...)` with columns matching `TruckRecord`; datetimes stored as ISO-8601 UTC text. `update_truck` allows only `TruckRecord` field names except `id`/`run_id`. Retention: sum all file sizes; while over limit delete `.mp4` files oldest-mtime first; if no `.mp4` remain, stop (images are kept) and log a warning.

- [ ] **Step 4: Run** `uv run pytest tests/test_storage.py -v` — Expected: 7 passed.

- [ ] **Step 5: Commit** — `git commit -m "feat: SQLite truck repository and media retention"`

---

### Task 7: Evidence (ring buffer, snapshots, clips)

**Files:**
- Create: `engine/events/__init__.py`, `engine/events/evidence.py`
- Test: `tests/test_evidence.py`

**Interfaces:**
- Consumes: `Frame`, `day_dir`.
- Produces:
  - `FrameRingBuffer(seconds: float)`: `push(frame: Frame)`, `since(ts: float) -> list[Frame]` (frames with `frame.ts >= ts`, oldest first). Keeps frames whose `ts >= newest.ts - seconds`.
  - `pick_h264_encoder() -> str` — first available of `["libx264", "h264_nvenc", "libopenh264", "h264_mf"]` per `av.codec.Codec(name, "w")`; raises `RuntimeError` listing all four if none.
  - `write_clip(frames: list[Frame], path: Path, fps: float) -> None` — H.264 MP4, `yuv420p`, even dimensions.
  - `save_snapshot(frame: Frame, box: tuple[float,float,float,float], full_path: Path, crop_path: Path) -> None` — JPEG quality 90; crop padded 15% each side, clamped to image.
  - `EvidenceRecorder(data_dir: Path, before_s: float, after_s: float)`: `push(frame: Frame)`; `start_clip(key: str, trigger_ts: float, path: Path, on_done: Callable[[Path], None])`; `flush()` (end of stream: finish all pending clips with the frames they have); `close()` (flush + join worker).

- [ ] **Step 1: Write failing tests**

```python
def test_ring_keeps_last_seconds():           # push 120 frames @30fps, seconds=3 → since(0) starts at ts >= newest-3
def test_h264_encoder_available():             # pick_h264_encoder() in the four names
def test_clip_has_before_and_after(tmp_path):
    # push frames 0..299 (10 s); start_clip at ts=4.0 after pushing frame 120
    # → clip decodes (av.open) with ~ (3+5)*30 = 240 frames ± 2, codec name "h264", on_done called with path
def test_clip_shorter_when_video_ends(tmp_path):
    # start_clip at ts=1.0 (only 1 s before available), flush() after 2 s more → clip ~ 90 frames, still written
def test_snapshot_and_padded_crop(tmp_path):   # box (100,100,140,120) on 320x240 → crop size (52, 26) ± 1, both files exist
def test_odd_frame_size_clip(tmp_path):        # 321x241 frames still encode
```

- [ ] **Step 2: Run** `uv run pytest tests/test_evidence.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

`EvidenceRecorder` owns a `FrameRingBuffer(before_s + after_s + 1)`. `start_clip` captures `ring.since(trigger_ts - before_s)` immediately and registers a pending job; every `push` appends the new frame to pending jobs; a job completes when `frame.ts >= trigger_ts + after_s` and is handed to a single background `ThreadPoolExecutor(max_workers=1)` that calls `write_clip` then `on_done(path)`. FPS for encoding = `(len-1)/(last.ts-first.ts)` falling back to `DEFAULT_FPS`. Encoding: `av.open(str(path), "w")`, stream from `pick_h264_encoder()` (cached), frames converted BGR→`yuv420p`, odd width/height cropped by 1 px.

- [ ] **Step 4: Run** `uv run pytest tests/test_evidence.py -v` — Expected: 6 passed.

- [ ] **Step 5: Commit** — `git commit -m "feat: evidence ring buffer, snapshots and H.264 clips"`

---

### Task 8: Event bus and event manager

**Files:**
- Create: `engine/events/bus.py`, `engine/events/manager.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: `Track`, `TrackerResult`, `Frame`, `TruckRepository`, `TruckRecord`, `EvidenceRecorder`, `save_snapshot`, `day_dir`, `enforce_retention`.
- Produces:
  - `EventBus`: `subscribe(maxsize: int = 100) -> queue.Queue[dict]`, `unsubscribe(q)`, `publish(msg: dict)` — never blocks; a full subscriber queue drops its oldest message.
  - `truck_to_json(rec: TruckRecord) -> dict` — keys: `api` (1), `id`, `run_id`, `track_id`, `first_seen`, `last_seen` (ISO-8601 Z), `best_score`, `snapshot_url`, `crop_url`, `clip_url` (`"/media/<relative path>"` URL-quoted, or `None`), `lat`, `lon`, `geo_error_m`, `geo_status`.
  - `EventManager(repo, evidence, bus, run_id: int, data_dir: Path, snapshot_window_s: float, max_disk_bytes: int)`: `on_frame(frame: Frame, result: TrackerResult) -> None`, `close() -> None`; property `active_count: int`.
  - Bus messages: `{"api":1,"type":"truck.new","truck":{...}}` and `{"api":1,"type":"truck.updated","truck":{...}}`.

- [ ] **Step 1: Write failing tests** (real repo + real recorder in `tmp_path`)

```python
def test_bus_fanout():                          # 2 subscribers both get the message
def test_bus_slow_subscriber_drops_oldest():    # maxsize=2, publish 3 → queue holds msgs 2 and 3; publish never blocks
def test_new_confirmed_track_creates_one_record(tmp_path):
    # same track_id confirmed on 10 frames → repo has 1 truck, bus got exactly 1 truck.new
def test_snapshot_improves_within_window(tmp_path):
    # score .5 at ts 0, .9 at ts 1.0, .99 at ts 2.5 → best_score == .9 (2.5 s is outside the 2 s window)
def test_ended_track_sets_last_seen_and_publishes_update(tmp_path)
def test_clip_path_saved_when_clip_done(tmp_path):
    # after evidence.flush(): record.clip_path ends with ".mp4" and file exists; a truck.updated carries clip_url
def test_media_urls_quoted(tmp_path):            # data dir with a space → urls contain no raw spaces
```

- [ ] **Step 2: Run** `uv run pytest tests/test_events.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

On first confirmation of a `track_id`: build a `TruckRecord` (`first_seen` = frame `wall_time`), save snapshot + crop to `day_dir/<run>_<track>_full.jpg` and `_crop.jpg`, insert, `evidence.start_clip(...)` to `<run>_<track>.mp4` with `on_done` updating `clip_path`, running `enforce_retention(media_dir, max_disk_bytes)` and publishing `truck.updated`; publish `truck.new`. While `frame.ts - first_ts <= snapshot_window_s`, a higher score overwrites the snapshot files and `best_score`. Every frame updates in-memory `last_seen`; on `ended_ids` write `last_seen` to the repo and publish `truck.updated`. `close()` writes `last_seen` for all active tracks and calls `evidence.close()`.

- [ ] **Step 4: Run** `uv run pytest tests/test_events.py -v` — Expected: 7 passed.

- [ ] **Step 5: Commit** — `git commit -m "feat: event bus and truck event manager"`

---

### Task 9: Pipeline (threads, annotation, status)

**Files:**
- Create: `engine/pipeline.py`, `engine/annotate.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything above. `trucks_now` = `events.active_count`; `trucks_total` = `repo.count_trucks(run_id)`.
- Note: the spec's example config shows `input_size: 960`; this plan defaults to **512** (RF-DETR Small's native size). The best size is picked by benchmark in Plan 6.
- Produces:
  - `@dataclass PipelineStatus(source_state: str, fps: float, frames_processed: int, trucks_now: int, trucks_total: int, started_at: datetime, telemetry_state: str = "none")` — `source_state` ∈ `"starting" | "running" | "ended" | "error"`; `fps` = processed frames/s over the last 2 s.
  - `RuntimeSettings` (pydantic): `confidence: float` (0–1 exclusive), `sahi_enabled: bool = False` (stored; used in Plan 6).
  - `Pipeline(source: VideoSource, detector: Detector, tracker: Tracker, events: EventManager, recorder: EvidenceRecorder, runtime: RuntimeSettings, realtime: bool, stream_max_width: int = 1280)`: `start()`, `stop()`, `wait(timeout: float | None = None) -> bool` (True when finished), `status() -> PipelineStatus`, `latest_jpeg() -> bytes | None`.
  - `annotate(frame: Frame, tracks: list[Track]) -> np.ndarray` — supervision `BoxAnnotator` + `LabelAnnotator`, label `"truck #<id> 0.87"`.

- [ ] **Step 1: Write failing tests**

```python
def test_end_to_end_two_trucks(tmp_path):
    # make_video 180 frames (6 s @30fps); FakeDetector: truck A frames 10..120, truck B frames 60..179 (until last frame)
    # Pipeline(realtime=False) → wait(30) is True
    assert repo.count_trucks(run_id) == 2
    # both records: snapshot, crop and clip files exist (B's clip shorter: video ended)
    # bus received exactly 2 "truck.new"
    assert pipeline.status().source_state == "ended" and pipeline.status().frames_processed == 180

def test_runtime_confidence_applied(tmp_path):  # runtime.confidence = 0.9 → detector.confidence == 0.9 on next frame
def test_latest_jpeg_available(tmp_path):        # after start, within 2 s latest_jpeg() decodes (cv2.imdecode) to width <= stream_max_width
def test_realtime_drops_frames_not_lag(tmp_path):
    # realtime=True, FakeDetector sleeps 0.1 s per call, 90-frame video (3 s) → finishes in < 4.5 s and frames_processed < 90
def test_detector_exception_does_not_kill_pipeline(tmp_path):
    # FakeDetector raises on call 5 → frame skipped, error logged, pipeline still reaches "ended"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_pipeline.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

Capture thread: `source.read()` loop; `realtime=True` → write to a one-slot holder guarded by a `threading.Condition` (overwrite); `realtime=False` → `queue.Queue(maxsize=8)` (blocking put). Every captured frame also goes to `recorder.push` **from the capture thread** so clips keep full frame rate even when inference skips frames. Inference thread: take frame → set `detector.confidence = runtime.confidence` → detect → `tracker.update` → `events.on_frame` → annotate → resize to `stream_max_width` → `cv2.imencode(".jpg", quality 80)` into `latest_jpeg`. When the source ends: drain, `events.close()`, `source_state = "ended"`. Exceptions in detect/track are logged with `logging.exception` and the frame is skipped; a `torch.cuda.OutOfMemoryError` additionally logs the hint "lower detector.input_size or use size: nano" (once per run).

- [ ] **Step 4: Run** `uv run pytest tests/test_pipeline.py -v` — Expected: 5 passed.

- [ ] **Step 5: Commit** — `git commit -m "feat: threaded pipeline with live-first frame dropping"`

---

### Task 10: HTTP / WebSocket / MJPEG API

**Files:**
- Create: `engine/api/__init__.py`, `engine/api/app.py`, `engine/api/auth.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `Pipeline`, `PipelineStatus`, `RuntimeSettings`, `TruckRepository`, `truck_to_json`, `EventBus`, `ApiConfig`.
- Produces:
  - `@dataclass EngineContext(pipeline: Pipeline, repo: TruckRepository, bus: EventBus, runtime: RuntimeSettings, data_dir: Path, api: ApiConfig, token: str | None)`
  - `create_app(ctx: EngineContext) -> FastAPI`
  - Routes (all JSON includes `"api": 1`):
    - `GET /` → `{"api":1,"name":"sky-eye","version":"0.1.0"}` (Plan 2 replaces with the dashboard)
    - `GET /api/status` → `{"api":1,"status":{...PipelineStatus, datetimes ISO Z}}`
    - `GET /api/trucks?from=&to=&limit=&offset=` → `{"api":1,"trucks":[...],"limit":..,"offset":..}`; `limit` 1–500 (default 50)
    - `GET /api/trucks/{id}` → `{"api":1,"truck":{...}}`, 404 when missing
    - `GET /media/{path}` → static files from `data_dir/media` (no path traversal)
    - `GET /stream.mjpg` → `multipart/x-mixed-replace; boundary=frame`, at most `api.stream_fps` frames/s
    - `WS /ws` → forwards bus messages; plus `{"api":1,"type":"status","status":{...}}` every 1 s
    - `GET /api/config/runtime`, `PUT /api/config/runtime` → `{"api":1,"runtime":{"confidence":..,"sahi_enabled":..}}`; invalid body → 422
  - Auth (`api.auth.mode == "token"`): every route except `GET /` requires header `X-SkyEye-Token` or query `?token=` equal to `SKYEYE_API_TOKEN`; else 401 (WS closes with code 4401).

- [ ] **Step 1: Write failing tests** (FastAPI `TestClient`, `EngineContext` with a fake pipeline object exposing `status()` and `latest_jpeg()`)

```python
def test_status_shape(client)                    # 200, body["api"] == 1, "fps" in body["status"]
def test_trucks_list_and_detail(client, repo)    # 2 inserted → list len 2 newest first; detail 200; /api/trucks/999 → 404
def test_limit_bounds(client)                    # limit=0 → 422; limit=501 → 422
def test_media_url_served_from_dir_with_spaces(tmp_path)  # data_dir "auto detection/data"; GET truck["snapshot_url"] → 200 image/jpeg
def test_media_path_traversal_blocked(client)    # GET /media/../sky-eye.db → 404
def test_runtime_put_and_get(client)             # PUT {"confidence":0.6} → GET returns 0.6; PUT {"confidence":2} → 422
def test_ws_receives_truck_new(client, bus)      # connect, bus.publish(truck.new msg) → received
def test_ws_disconnect_does_not_break_bus(client, bus)  # connect, close abruptly, then bus.publish 200 msgs → no exception, subscriber removed
def test_mjpeg_first_part(client)                # stream response starts with b"--frame" and contains b"Content-Type: image/jpeg"
def test_token_mode(tmp_path, monkeypatch)       # mode token: /api/status without token 401; with header 200; with ?token= 200; GET / 200
```

- [ ] **Step 2: Run** `uv run pytest tests/test_api.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement**

`StaticFiles(directory=media_dir)` mounted at `/media` (it rejects traversal). WS handler: `q = bus.subscribe()`; loop reading `q` via `asyncio.to_thread(q.get, timeout=1.0)` and sending status on each 1 s tick; `finally: bus.unsubscribe(q)`. MJPEG: async generator that yields `latest_jpeg()` when it changed, sleeping `1/stream_fps`. Auth as a dependency + WS check before `accept()`. `PUT /api/config/runtime` validates with `RuntimeSettings` and updates the shared instance in place.

- [ ] **Step 4: Run** `uv run pytest tests/test_api.py -v` — Expected: 10 passed.

- [ ] **Step 5: Commit** — `git commit -m "feat: REST, WebSocket and MJPEG API"`

---

### Task 11: CLI entry point, wiring and first real run

**Files:**
- Create: `engine/app.py`, `engine/logging_setup.py`, `README.md` (replace)
- Modify: `engine/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: all previous tasks.
- Produces:
  - `build_engine(cfg: AppConfig) -> EngineContext` (creates data dir, repo, run row with the config JSON, source, detector, tracker using `source.fps`, recorder, manager, pipeline).
  - `main(argv: list[str] | None = None) -> int` — `python -m engine --config PATH [--check]`. `--check` loads config + builds detector, prints `OK` and exits 0. Exit code **2** with a one-paragraph message (no traceback) for `ConfigError` / `DetectorError`. Otherwise starts pipeline + uvicorn, prints `Dashboard: http://<LAN-IP>:<port>/` (LAN IP via a UDP socket trick, fallback `127.0.0.1`), Ctrl+C stops pipeline cleanly.
  - Logging: console + rotating file `data/logs/sky-eye.log` (5 MB × 3).

- [ ] **Step 1: Write failing tests**

```python
def test_bad_config_exit_code_2(tmp_path, capsys)        # confidence 1.5 → main([...]) == 2, stderr mentions "detector.confidence", no "Traceback"
def test_missing_weights_exit_code_2(tmp_path, capsys)   # weights: nope.pth → 2, message has the path
def test_build_engine_with_fake_detector(tmp_path, monkeypatch)
    # monkeypatch build_detector → FakeDetector; build_engine(cfg) → ctx.repo has 1 run; ctx.pipeline.status().source_state == "starting"
```

- [ ] **Step 2: Run** `uv run pytest tests/test_cli.py -v` — Expected: FAIL.

- [ ] **Step 3: Implement** `engine/app.py`, `engine/__main__.py`, `engine/logging_setup.py` as specified above; README "Quick start": install uv, `uv sync`, copy `config.example.yaml` → `config.yaml`, put a drone clip at `samples/drone.mp4`, `uv run python -m engine --config config.yaml`, open the printed URL + `/stream.mjpg`.

- [ ] **Step 4: Run** `uv run pytest -v` — Expected: all tests pass (the gpu-marked ones are deselected).

- [ ] **Step 5: Manual GPU smoke run**

Put any drone clip with visible trucks at `samples/drone.mp4` (owner supplies one, e.g. a downloaded drone highway clip). Run `uv run python -m engine --config config.yaml`.
Expected: printed dashboard URL; `http://localhost:8000/stream.mjpg` shows boxes labelled `truck #<id>`; `http://localhost:8000/api/trucks` lists trucks; `data/media/<today>/` holds `_full.jpg`, `_crop.jpg`, `.mp4` per truck; clips play in the browser; `/api/status` `fps` value recorded in the commit message. Open the URL from a phone on the same Wi-Fi (Windows may ask to allow Python through the firewall — allow on **Private** networks only).

- [ ] **Step 6: Commit**

```bash
git add engine tests README.md
git commit -m "feat: CLI entry point and engine wiring (smoke run: <fps> FPS on RTX 4050)"
```
