# Plan 1 (engine core): follow-ups

Plan 1 is complete and reviewed. This file lists what was deliberately left open, so later plans pick it up.

## Carry into later plans

| Plan | Item |
|---|---|
| 2 (dashboard) | WebSocket token rejection closes before `accept`, so browsers see HTTP 403 / close code 1006, not 4401. Accept-then-close if the UI must tell auth failures apart |
| 2 | Token in `?token=` query lands in access logs; document it, or use a short-lived ticket |
| 2 | Snapshot is overwritten at the same URL when a better frame arrives: add cache-busting and publish `truck.updated` on best-score change |
| 2 | Add `movflags=faststart` to clips so browsers start playback before full download |
| 2 | `/api/status` lacks GPU / model info that the Status page needs (spec §5.7) |
| 2 | `start_clip` runs before `truck.new` is published, so a fast `truck.updated` could arrive first |
| 3 (alerts + live) | `CaptureSource` never reports `ended` when a device disappears; needs reconnect / error state |
| 3 | `CaptureSource` timestamps use `time.monotonic()` (15.6 ms resolution on Windows) and can repeat. Switch to `perf_counter` |
| 4 (training) | COCO class mapping is hard-coded: custom fine-tuned weights need their own class map |
| 4/6 | `high_conf_det_threshold=0.0` disables ByteTrack's low-score second stage. Revisit together with detector flicker (many short tracks on aerial footage) |
| 6 (speed) | Measure FPS on an idle machine (smoke runs: ~29 FPS on 5 Nov, ~3–5 FPS on a loaded machine on 6 Nov) |
| 6 | Clip cap (6 queued + pending jobs) is a constructor default, not a config key; offline (`realtime: false`) runs should apply back-pressure instead of skipping clips |

## Small residual fixes (any time)

- `config.py`: float bounds accept infinity (`max_disk_gb: .inf`, `lost_timeout_s: .inf`) and then crash with a traceback. Fix: `ConfigDict(allow_inf_nan=False)` on the base model.
- `app.py`: no regression test pins the clearing of uvicorn's private `_captured_signals` (pinned `uvicorn~=0.54`).
- `retention.remove_partial_clips` deletes every `*.mp4.part` at startup; skip recently modified files in case two engines share one `data_dir`.
- `pipeline.stop()` docstring still says only unstarted clips are dropped (the encode in progress is abandoned too).
- `_bind` binds IPv4 only for `""` or a hostname (the default `0.0.0.0` is fine).
- `EventManager.close()` skips `evidence.close()` if a repo write raises; wrap it in try/finally.
- Ctrl+C during model load prints a traceback.
- `clear_clip_paths` uses SQLite `RETURNING` (needs SQLite ≥ 3.35; bundled Python 3.12 has 3.49).
- Minor test gaps: tracker `build_tracker` and 60 fps cases, `status().fps` window, `stop()` in non-realtime mode with a blocked producer, bus concurrency, retention under day folders.
