# sky-eye Plan 4A — Tracking You Can Trust Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure how well sky-eye counts trucks, then change the tracking until each real truck is saved once and junk is not saved, proving every change with numbers.

**Architecture:**
- A new offline package, `engine/evaluation/`, does four things:
  - it caches raw detector output per video once on the GPU;
  - it replays those detections through the engine's own tracker code at any simulated frame rate;
  - it builds review sheets so a human or vision reviewer can label which tracks are the same real truck;
  - it scores any run against those labels.
- Tracker fixes go into `engine/tracking/` and `engine/detectors/`. Each fix lands behind a config option, is measured, and becomes the default only if the numbers improve.

**Tech Stack:** Python 3.12, the existing RF-DETR + `trackers` 2.7 + supervision + OpenCV + numpy stack. No new dependencies.

**Spec:**
- `docs/superpowers/specs/2026-11-05-sky-eye-phase1-design.md`: §5.2 detector, §5.3 tracker, §8 criterion 4 ("Each truck in a test clip is saved once (duplicate events ≤ 10% of trucks in the clip)").
- `OPUS_GUIDE.md` §6: the evidence and the fix plan this implements.

## Global Constraints

- **Success targets,** at default settings, on the labelled clips, at full frame rate *and* at a 10 fps processing budget:
  - duplicates ≤ 10% of true trucks;
  - missed trucks ≤ 10%;
  - false trucks ≤ 10%;
  - with the confidence slider at 0.2 the count is at most 1.5× the count at the default.

  If the stock COCO model cannot reach them, report the numbers honestly; training is Plan 4.
- **Measure first.** No default tracker or detector setting changes until Task 6 has recorded a baseline in `docs/results.md`. After that, every default change cites before/after numbers from `docs/results.md`.
- **The engine's behaviour at run time is what gets measured.** Replay must use the same detection filtering function and the same `build_tracker(...)` the pipeline uses; never a copy.
- **Never commit sample clips or images derived from them.** `samples/` clips carry a "DVIDEO" stock watermark.
  - `eval/cache/`, `eval/review/` and `eval/runs/` are git-ignored.
  - Only `eval/truth/*.json`, which is text with no image data, is committed.
- **No new Python or npm dependencies.**
- Every new config option has a default and is documented in `config.example.yaml`. Configs written for earlier versions keep loading.
- **Live-first stays:** detection never waits on evaluation code, and evaluation code never runs inside the engine.
- **Tests:** synthetic detection streams with exact ground truth; never port 8000; no GPU needed except tests marked `gpu` (deselected by default).

## Review Focus

1. **A truck that flickers below the confidence threshold for a few frames.** Expect one ID, not several. Test: Task 7, `test_low_score_frames_keep_the_track`.
2. **The camera pans while trucks are small.** Expect one ID per truck. Test: Task 9, `test_camera_pan_keeps_ids` (synthetic pan of 15 px/frame on 30 px boxes).
3. **The laptop is slow: 5–10 processed frames per second.** Expect the count not to explode. Test: every scoring run in Tasks 6–10 reports the 10 fps budget alongside full rate, and Task 4 has `test_replay_fps_budget_skips_frames`.
4. **The confidence slider is moved low during a live run.** Junk must not start tracks; only boxes above the slider start a track. Test: Task 7, `test_low_score_boxes_never_start_a_track`.
5. **The detector calls a truck "car" for a few frames.** Expect one truck, not a gap and a new ID. Test: Task 8, `test_class_flip_keeps_one_truck`.

---

### Task 1: Shared detection filter and raw detector output

**Files:**
- Modify: `engine/detectors/rfdetr.py`, `engine/core.py` (no interface change), `engine/pipeline.py:370-374`
- Create: `engine/detectors/filtering.py`
- Test: `tests/test_detection_filter.py`, existing `tests/test_detectors.py`, `tests/test_pipeline.py`

**Interfaces:**
- Produces:
  - `filter_detections(raw: list[Detection], classes: Sequence[str], min_score: float) -> list[Detection]` in `engine/detectors/filtering.py`. It keeps detections whose `class_name` is in `classes` and whose `score >= min_score`, in input order.
  - `RFDETRDetector.detect_raw(image_bgr, floor: float, class_names: Sequence[str]) -> list[Detection]`. It returns every detection of the named COCO classes with score ≥ `floor`.
  - `RFDETRDetector.detect` keeps its current behaviour, implemented as `detect_raw` at `self.confidence` followed by `filter_detections`.
- The pipeline still calls `detector.detect` after this task (no behaviour change).

- [ ] **Step 1:** Write `tests/test_detection_filter.py`:
  - `test_filter_keeps_classes_and_threshold`: keeps 0.5 truck and 0.4 truck at min 0.4; drops 0.39 truck and 0.9 car.
  - `test_filter_keeps_order`.
  - Run it; expect an ImportError.
- [ ] **Step 2:** Implement `filtering.py`. Make `detect` use `detect_raw` + `filter_detections`. Allow `class_names` in `detect_raw` to include any COCO name; unknown names raise `DetectorError`.
- [ ] **Step 3:** Run `python -m uv run pytest -q -p no:cacheprovider tests/test_detection_filter.py tests/test_detectors.py tests/test_pipeline.py`. Expect PASS.
- [ ] **Step 4:** Commit `refactor(detector): shared detection filter; raw detections for evaluation`.

### Task 2: Detection cache

**Files:**
- Create: `engine/evaluation/__init__.py`, `engine/evaluation/cache.py`, `engine/evaluation/__main__.py`
- Modify: `.gitignore` (add `eval/cache/`, `eval/review/`, `eval/runs/`)
- Test: `tests/test_eval_cache.py`

**Interfaces:**
- Consumes: Task 1 `detect_raw`.
- Produces:
  - `@dataclass DetectionCache(video: str, width: int, height: int, fps: float, model: str, input_size: int, floor: float, classes: tuple[str, ...], frames: list[CachedFrame])`
  - `@dataclass(frozen=True) CachedFrame(index: int, ts: float, detections: tuple[Detection, ...])`
  - `build_cache(video: Path, detector, floor: float, classes: Sequence[str]) -> DetectionCache` runs the detector on **every** frame, in order, with `ts = index / fps`.
  - `save_cache(cache, path: Path)` and `load_cache(path: Path) -> DetectionCache` use one `.npz` (numpy arrays + a JSON header). The round trip must be exact.
  - CLI: `python -m engine.evaluation cache --video <path> --size <px> [--model small] [--floor 0.05] [--classes car,truck,bus] [--out eval/cache/<stem>__<model>_<size>.npz]`. It prints frames and seconds, and shows progress every 100 frames.

- [ ] **Step 1:** Tests:
  - `test_cache_round_trip`: a hand-built cache with 3 frames, including one with zero detections, saved and loaded, compares equal.
  - `test_build_cache_every_frame`: a 12-frame generated video (OpenCV) and a fake detector that returns one box per frame give `len(frames) == 12` and `ts == index / fps`.
  - Run them; expect FAIL.
- [ ] **Step 2:** Implement. Read frames with OpenCV in order; no realtime pacing and no dropping.
- [ ] **Step 3:** Tests PASS. Run the CLI once on `samples/drone.mp4 --size 512` and record seconds and frames in the report. This is a GPU run, not a test.
- [ ] **Step 4:** Commit `feat(eval): cache raw detections per video`.

### Task 3: Synthetic detection streams with exact ground truth

**Files:**
- Create: `engine/evaluation/synthetic.py`
- Test: `tests/test_eval_synthetic.py`

**Interfaces:**
- Produces: `make_stream(spec: StreamSpec, seed: int = 0) -> tuple[DetectionCache, dict[int, int]]`.
  - It returns a cache-shaped stream and the true identity of each detection, keyed by `id(detection)`. Truth IDs are 0..n−1.
  - `StreamSpec` fields, all with defaults:
    - `n_trucks=3`, `n_cars=2`, `seconds=10.0`, `fps=30.0`
    - `size_px=40`, `speed_px_s=60.0`, `pan_px_s=0.0` (camera pan, shifting every box)
    - `score=0.7`, `low_score_runs: list[tuple[int, float, float, float]] = []` (truth id, start s, end s, score during the run)
    - `class_flips: list[tuple[int, float, float, str]] = []` (truth id, start s, end s, class name)
    - `dropouts: list[tuple[int, float, float]] = []` (truth id, start s, end s: no detection)
    - `junk_per_s=0.0` (one-frame random boxes at score 0.15–0.35)
    - `frame=(1280, 720)`
  - Deterministic for a given seed.
- Later tasks use it to prove each fix with exact truth.

- [ ] **Step 1:** Tests:
  - `test_stream_is_deterministic`;
  - `test_low_score_run_sets_scores`;
  - `test_class_flip_sets_class`;
  - `test_dropout_removes_detections`;
  - `test_pan_shifts_all_boxes`;
  - `test_junk_has_no_identity` (junk detections are absent from the identity map).

  Expect FAIL.
- [ ] **Step 2:** Implement. Trucks enter from one edge on straight lines with no overlap between them unless the spec asks for it.
- [ ] **Step 3:** PASS. Commit `test(eval): synthetic detection streams with exact identities`.

### Task 4: Replay through the engine's tracker

**Files:**
- Create: `engine/evaluation/replay.py`
- Modify: `engine/evaluation/__main__.py` (add the `replay` command)
- Test: `tests/test_eval_replay.py`

**Interfaces:**
- Consumes:
  - Task 1 `filter_detections`;
  - Task 2 `DetectionCache` / `load_cache`;
  - Task 3 `make_stream`;
  - `engine.tracking.build_tracker(cfg: TrackerConfig, fps: float)`;
  - `AppConfig` / `load_config`.
- Produces:
  - `@dataclass TruckTrack(track_id: int, first_ts: float, last_ts: float, hits: int, best_score: float, boxes: list[tuple[float, tuple[float,float,float,float]]])`. `boxes` holds `(ts, box)` for every confirmed frame.
  - `replay(cache: DetectionCache, cfg: AppConfig, confidence: float | None = None, fps_budget: float | None = None) -> list[TruckTrack]`
    - Filters each frame with `filter_detections(frame.detections, cfg.detector.classes, confidence or cfg.detector.confidence)`. **Later tasks change this call** (Task 7 and Task 8) together with the pipeline, through the same function. The **detection step** must therefore be one function, `prepare_detections(raw, cfg, confidence)` in `engine/detectors/filtering.py`, used by both the pipeline and replay. Task 4 creates it as a thin wrapper of `filter_detections` and switches the pipeline to `detector.detect_raw` + `prepare_detections`.
    - Runs `build_tracker(cfg.tracker, cache.fps)` and builds Frames with `ts` from the cache and a dummy 1×1 image. A tracker may not read pixels unless Task 9 says so.
    - Records every track while it is confirmed.
    - `fps_budget`: frame k is processed only if `ts_k >= next_slot`, and then `next_slot = ts_k + 1/fps_budget`. This simulates a slow laptop dropping frames.
  - `summarize(tracks) -> dict` with:
    - `trucks`: number of tracks;
    - `median_s`, `under_1s_pct`, `under_2s_pct`, `max_s`;
    - `best_score_median`.
  - CLI: `python -m engine.evaluation replay --cache <npz> --config <yaml> [--confidence X] [--fps-budget N] [--out eval/runs/<name>.json]`. It writes the tracks plus a summary, and prints the summary as one table row.

- [ ] **Step 1:** Tests, using `make_stream`:
  - `test_replay_counts_clean_stream`: 3 trucks, no noise, default config gives 3 tracks.
  - `test_replay_fps_budget_skips_frames`: 30 fps stream with budget 10 processes 1/3 of frames ±1.
  - `test_replay_uses_prepare_detections` (a monkeypatched spy is called once per processed frame).
  - `test_pipeline_uses_prepare_detections` (the same spy through a Pipeline rig).

  Expect FAIL.
- [ ] **Step 2:** Implement. The pipeline change must keep every existing pipeline test green.
- [ ] **Step 3:** PASS; full suite once. Commit `feat(eval): replay cached detections through the engine tracker`.

### Task 5: Review sheets and truth format

**Files:**
- Create: `engine/evaluation/reference.py`, `engine/evaluation/sheets.py`, `engine/evaluation/truth.py`
- Modify: `engine/evaluation/__main__.py` (add the `sheets` command)
- Test: `tests/test_eval_truth.py`

**Interfaces:**
- Produces:
  - `reference_tracks(cache) -> list[TruckTrack]`: a permissive run used only for labelling.
    - Every frame; all cached classes; score ≥ 0.1; ByteTrack with `confirm_hits=1`, `confirm_window=1`, `lost_timeout_s=3.0`.
    - It must be deterministic. Its tracks are numbered `R1, R2, …` in order of first appearance, and stored with the class votes per frame.
  - **Candidates:** reference tracks with at least one detection labelled `truck` at score ≥ 0.2.
  - Truth file `eval/truth/<video stem>.json`. The labeller fills `trucks`, `not_truck` and `unsure`:

    ```json
    {"video": "drone3.mp4", "frames": 1483, "fps": 29.97, "cache": "drone3__small_512.npz",
     "reference": {"floor": 0.1, "lost_timeout_s": 3.0},
     "candidates": ["R1", "R4", ...],
     "trucks": [{"id": "T1", "refs": ["R1", "R9"], "note": "white box truck, left lane"}],
     "not_truck": ["R4"], "unsure": [],
     "labelled_by": "", "labelled_on": ""}
    ```

    Every candidate must appear exactly once across `trucks[].refs`, `not_truck` and `unsure`.
  - `load_truth(path) -> Truth` validates that rule. A missing candidate, a duplicate, or an unknown ref raises `ValueError` naming it.
  - `sheets(cache, video_path, out_dir)` writes into `eval/review/<stem>/`:
    - one PNG per group of 4 candidates. Each candidate gets a row of 6 crops: evenly spaced in time, box expanded 1.5×, upscaled to 128 px tall. Its label shows the ref id, `t=start–end s`, class votes and the best truck score.
    - one `timeline.png`: candidates as horizontal bars by time, with the x-position of the box centre written on each bar, so that pieces of one truck can be matched.
    - `truth_template.json`, prefilled with `candidates`.
- CLI: `python -m engine.evaluation sheets --cache <npz> --video <path>`.

- [ ] **Step 1:** Tests:
  - `test_reference_tracks_are_deterministic` (synthetic stream run twice gives the same `R` ids);
  - `test_candidates_need_a_truck_label`;
  - `test_load_truth_rejects_missing_candidate`, `test_load_truth_rejects_duplicate`, `test_load_truth_accepts_complete`;
  - `test_sheets_writes_pngs_and_template` (a tiny generated video and synthetic cache in `tmp_path`).

  Expect FAIL.
- [ ] **Step 2:** Implement with OpenCV drawing only.
- [ ] **Step 3:** PASS. Commit `feat(eval): reference tracks, review sheets, truth format`.

### Task 6: Scoring and the baseline

**Files:**
- Create: `engine/evaluation/score.py`, `docs/results.md`
- Modify: `engine/evaluation/__main__.py` (add the `score` command and `bench`, which runs a grid)
- Test: `tests/test_eval_score.py`

**Interfaces:**
- Consumes: Tasks 4–5.
- Produces:
  - `score(run: list[TruckTrack], reference: list[TruckTrack], truth: Truth) -> Scores` with fields:
    - `true_trucks`, `counted`, `matched_trucks`;
    - `duplicates`, `missed`, `false`, `unverified`;
    - the percentages of `true_trucks`.
  - **Matching rule** for each run track:
    - Find the reference track with the highest mean IoU over the timestamps they share. It must share ≥ 50% of the run track's timestamps, with mean IoU ≥ 0.3.
    - Matched to a ref inside a truck group → counts for that truck. Several run tracks on one truck → `duplicates += n − 1`.
    - Matched to `not_truck`, or to a non-candidate reference → `false`.
    - Matched to `unsure` → ignored.
    - No reference match → `unverified`.
  - `missed` = truck groups with no run track.
  - `bench --truth eval/truth --caches eval/cache --config <yaml> [--set key=value ...]` scores every labelled clip:
    - at full rate and at `--fps-budget 10`;
    - at the config's confidence and at 0.2.

    It prints one Markdown table: clip, setting, true, counted, duplicates %, missed %, false %, unverified, median track s.
- **Truth labels:** the controller produces them, not this task (see "Labelling" below). This task's tests use synthetic truth.

- [ ] **Step 1:** Tests on synthetic streams with truth built from the stream's identities:
  - `test_score_perfect_run`: 0 duplicates, 0 missed, 0 false.
  - `test_score_counts_duplicates` (one truck split into 2 run tracks gives duplicates 1).
  - `test_score_counts_false_and_missed`.
  - `test_score_unverified_when_no_reference`.

  Expect FAIL.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** PASS. Commit `feat(eval): score runs against truth labels`.
- [ ] **Step 4 (after labelling):** Run `bench` with the current defaults and write `docs/results.md`:
  - a short "How this is measured" paragraph;
  - the baseline table;
  - the targets from Global Constraints;
  - a "Changes" section that later tasks append to.

  Commit `docs: tracking baseline`.

**Labelling (controller step between Task 5 and Task 6 Step 4):**
1. Cache all three sample clips at size 512 (Task 2 CLI) and generate the sheets.
2. A vision-capable subagent fills each truth file from the sheets, marking anything it can't decide as `unsure`.
3. The controller spot-checks at least 10 decisions per clip against the sheets.
4. The truth JSON files are committed as `test(eval): truth labels for sample clips (machine-assisted, spot-checked)`.
5. The owner's own count of trucks per clip, when given, is recorded in `docs/results.md` next to the labelled count.

### Task 7: Low-score boxes keep tracks alive; stricter confirmation

**Files:**
- Modify: `engine/config.py`, `engine/detectors/filtering.py` (`prepare_detections`), `engine/tracking/bytetrack.py`, `engine/pipeline.py`, `config.example.yaml`, `README.md` (tracking settings only)
- Test: `tests/test_tracking.py`, `tests/test_eval_replay.py`

**Interfaces:**
- **Config:**
  - `DetectorConfig.min_confidence: float = 0.1` (0 < x ≤ confidence). This is the score floor for boxes passed to the tracker.
  - `TrackerConfig.confirm_min_s: float = 0.0` (≥ 0). A track becomes a truck only after it has lasted this long.
  - `TrackerConfig.confirm_needs_high_score: bool = True`. Its best score must have reached the confidence setting.
- **Behaviour:**
  - `prepare_detections` keeps boxes ≥ `min_confidence`.
  - The tracker gets the live confidence through a new method, `ByteTrackTracker.set_confidence(c: float)`, which the pipeline calls each frame instead of setting `detector.confidence`.
  - Only boxes ≥ confidence can **start** a track (`track_activation_threshold` / `high_conf_det_threshold` = confidence). Lower boxes can only continue one, through ByteTrack's second association stage.
  - Confirmation requires the existing hits rule **and** `confirm_min_s` **and**, if enabled, best score ≥ confidence.
- `Tracker` protocol: add `set_confidence(c: float) -> None` to the Protocol in `engine/core.py`, and implement it in every tracker, including the test fakes.

- [ ] **Step 1:** Tests:
  - `test_low_score_frames_keep_the_track`: synthetic truck at 0.7 with a 0.5 s run at 0.2, confidence 0.4, `min_confidence` 0.1, gives 1 track.
  - `test_low_score_boxes_never_start_a_track`: junk at 0.15–0.35 and no trucks gives 0 tracks.
  - `test_confirm_min_s_delays_confirmation`.
  - `test_confirm_needs_high_score`.
  - `test_slider_low_does_not_flood`: synthetic `junk_per_s=20`; confidence 0.2 vs 0.4, track count at 0.2 ≤ 1.5× the count at 0.4 + 2.
  - Old configs without the new keys still load.

  Expect FAIL.
- [ ] **Step 2:** Implement. The `trackers` ByteTrack thresholds must be updatable after construction. If the library copies them at init, update the attributes it reads, or rebuild only the thresholds. Never reset existing tracks.
- [ ] **Step 3:** PASS; full suite. Run `bench` with defaults and with `--set tracker.confirm_min_s=0.5`, and append both to `docs/results.md`.
- [ ] **Step 4:** Commit `feat(tracking): low-score boxes extend tracks; stricter confirmation`. The **controller** decides the defaults from the numbers. If a default changes, the change goes in a separate commit `chore(tracking): defaults from Plan 4A measurements`, which cites the table.

### Task 8: Track all vehicles, decide "truck" per track

**Files:**
- Modify: `engine/config.py`, `engine/detectors/filtering.py`, `engine/tracking/bytetrack.py`, `engine/events/manager.py` (only if it reads `class_name`), `config.example.yaml`
- Test: `tests/test_tracking.py`, `tests/test_events.py`

**Interfaces:**
- **Config:**
  - `DetectorConfig.track_classes: list[str] = []`. Empty means the same as `classes`. Otherwise all these classes go to the tracker.
  - `TrackerConfig.truck_vote_min: float = 0.4` (0–1).
- **Behaviour:**
  - Each track counts its hits per class.
  - It is reported as confirmed (a truck) only when the confirmation rules hold **and** `truck_hits / hits ≥ truck_vote_min` **and** its best *truck-labelled* score ≥ confidence.
  - Once a track is a truck it stays a truck.
  - `Track.detection` for a confirmed truck is the current frame's detection (any class). The event manager must not depend on `class_name`. Check it, and add a test if it does.

- [ ] **Step 1:** Tests:
  - `test_class_flip_keeps_one_truck`: a truck labelled car for 0.5 s mid-track gives 1 track, never ended and restarted.
  - `test_car_is_never_a_truck`: a stream car with 2 single-frame truck labels never confirms at vote 0.4.
  - `test_default_track_classes_matches_classes` (behaviour unchanged when the option is empty).

  Expect FAIL.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** PASS; full suite. Run `bench` with `--set detector.track_classes=[car,truck,bus]`, and append to `docs/results.md`.
- [ ] **Step 4:** Commit `feat(tracking): track all vehicles, vote on truck per track`. The controller rules on the default from the numbers.

### Task 9: Camera-motion-aware tracker choice

**Files:**
- Modify: `engine/config.py`, `engine/tracking/bytetrack.py` (generalise the wrapper so it can wrap any `trackers` tracker), `engine/tracking/__init__.py`, `config.example.yaml`
- Test: `tests/test_tracking.py`

**Interfaces:**
- `TrackerConfig.type: Literal["bytetrack", "botsort", "ocsort", "cbiou"] = "bytetrack"` and `TrackerConfig.camera_motion: bool = False`.
  - `camera_motion` maps to `enable_cmc` for BoT-SORT and OC-SORT. It is rejected with a config error for types that lack it.
- Our confirmation, voting, lost-timeout and confidence rules wrap whichever library tracker is chosen. The wrapper must not depend on ByteTrack-only internals. If a library tracker needs pixels for camera-motion compensation, give it `frame.image`. In that case replay must use real frames: the cache keeps a path to the video, and replay decodes frames only when the chosen tracker needs them.
- **Speed:** measure `update()` time per frame for each type on drone3 and record it. Camera-motion compensation costs CPU.

- [ ] **Step 1:** Tests:
  - `test_each_type_tracks_a_clean_stream` (parametrised over types);
  - `test_camera_pan_keeps_ids`: `pan_px_s=450`, 30 px boxes, `camera_motion` on for botsort; ID switches ≤ 1 per truck. This test may use generated frames with a textured background, so that compensation has something to see.
  - `test_camera_motion_rejected_for_bytetrack`.

  Expect FAIL.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** PASS; full suite. Run `bench` for each type, with and without `camera_motion`, at full rate and 10 fps. Append the results and the per-frame cost to `docs/results.md`.
- [ ] **Step 4:** Commit `feat(tracking): choose the tracker; camera-motion compensation`. The controller rules on the default.

### Task 10: Re-link broken tracks

**Files:**
- Modify: `engine/config.py`, the tracker wrapper, `config.example.yaml`
- Test: `tests/test_tracking.py`

**Interfaces:**
- `TrackerConfig.relink_s: float = 0.0` (0 = off). When a confirmed track is lost, its ID is held for `relink_s` seconds before being reported in `ended_ids`.
- A new library track that starts during that window is given the held truck ID instead of a new one when all of these hold:
  - its first box centre is within `max(2 × box diagonal, 40 px)` of the lost track's last centre, moved on by its last velocity × gap;
  - its box area ratio to the lost track's is between 0.5 and 2.0.

  The held ID is then reused and no new truck is created. If several candidates qualify, the nearest wins.
- `ended_ids` is reported late by up to `relink_s`. The event manager already handles late ends; add a test that the clip and `last_seen` are still right.

- [ ] **Step 1:** Tests:
  - `test_relink_joins_a_dropout`: a synthetic truck with a 1.5 s dropout and `relink_s=3` gives 1 truck.
  - `test_relink_ignores_far_new_track`.
  - `test_relink_off_by_default`.
  - `test_end_reported_after_window`.

  Expect FAIL.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** PASS; full suite. Run `bench` with `relink_s` 0, 2 and 4, and append to `docs/results.md`.
- [ ] **Step 4:** Commit `feat(tracking): re-link a truck lost for a moment`. The controller rules on the default.

### Task 11: Detector input size, final defaults, documentation

**Files:**
- Modify: `engine/config.py` (defaults only, as ruled), `config.example.yaml`, `README.md` (a "Tracking settings" section), `docs/results.md`, `OPUS_GUIDE.md` (status board + §6 outcome)
- Test: existing suites + e2e

- [ ] **Step 1:** Cache drone3 at the next valid input sizes above 512 that RF-DETR Small accepts (check the error message; e.g. 576, 640 or 704) and record seconds per frame.
- [ ] **Step 2:** Run `bench` at each size with the best settings so far. Record the counts, the scores, and the engine frames per second at that size on the RTX 4050, measured with the live engine for 30 s on drone3 and taken from `/api/status`.
- [ ] **Step 3:** The controller rules on the final defaults, using the targets in Global Constraints. Apply them, and write the final table plus an honest summary in `docs/results.md`: which targets are met, which are not, and why.
- [ ] **Step 4:** README "Tracking settings": what each option does, in plain words, plus the "accurate counting mode" (`source.realtime: false` for recorded files).
- [ ] **Step 5:** Full suite; `npm test`; `npm run build`; `pytest -m dashboard` 3 times. Commit `docs: tracking results and settings`.
