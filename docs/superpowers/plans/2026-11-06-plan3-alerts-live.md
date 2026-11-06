# sky-eye Plan 3 — Alerts + Live Drone Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read real live drone video (RTSP/RTMP/SRT/UDP streams, go2rtc relay, HDMI capture with replug), survive link drops, and alert people about new trucks on the laptop, in Telegram and over MQTT, with the website showing source health and letting the operator switch alert channels.

**Architecture:** Sources gain a `state` ("running" | "reconnecting") the pipeline surfaces as `source_state`. A new `engine/alerts/` package subscribes to the existing `EventBus`, applies a per-channel cooldown with grouping, and hands batches to `Notifier`s on a worker thread with bounded retries — detection never waits on alerts. Truck records gain `clip_status` and periodic `last_seen` updates so the website stops guessing.

**Tech Stack:** PyAV 19 (FFmpeg) for streams, OpenCV for capture, `plyer` (MIT) desktop notifications, `httpx` for the Telegram Bot API, `paho-mqtt` 2.x (EPL-2.0 OR BSD-3-Clause), existing FastAPI + React dashboard.

**Spec:** `docs/superpowers/specs/2026-11-05-sky-eye-phase1-design.md` (§5.1 sources, §5.6 alerts, §5.7 runtime settings). Follow-ups: `docs/superpowers/plan1-followups.md` and `docs/superpowers/plan2-followups.md` (Plan 3 rows).

## Global Constraints

- **Detection never waits on alerts or a dead stream:** notifier I/O runs off the inference thread; the alert queue is bounded (100) and drops the oldest batch with a WARNING when full.
- Stream reconnect backoff **1 s → 2 s → 4 s … capped at 30 s**, reset after a successful frame; the pipeline stays alive while reconnecting.
- Alerts: at most **one alert per channel every `cooldown_s` (default 10)**; trucks arriving inside the window are grouped into one message ("3 new trucks"). A failing channel retries **3×** with backoff **1 s, 2 s, 4 s**, then logs an ERROR and drops that batch.
- **Secrets only in `.env`**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `MQTT_PASSWORD`. Never logged, never returned by the API, never in `config.yaml`. Any logged URL/exception text that could contain the bot token is redacted (`bot***`).
- Every engine JSON body/message carries `"api": 1`; dashboard rules from Plan 2 hold (only react/react-dom at runtime, no external requests, 375 px, token on media/WS URLs, local times).
- Default install stays AGPL-free; new deps: `plyer~=2.1`, `paho-mqtt~=2.2` (`httpx` is already a dependency of the project — move it from dev to runtime deps).
- Code stays cross-platform: Windows-only calls (`winsound`) are guarded.

## Review Focus

1. **Drone link drops mid-flight** → `source_state` becomes `"reconnecting"`, the dashboard shows "Drone video lost — reconnecting…", the engine reconnects with backoff and resumes; nothing crashes. Test: Task 1 `test_stream_reconnects_with_backoff`, Task 2 `test_pipeline_reports_reconnecting`.
2. **No internet / Telegram down** → detection FPS unaffected, retries bounded, queue bounded. Test: Task 3 `test_failing_channel_never_blocks_publish`, `test_queue_full_drops_oldest`.
3. **Burst of 20 trucks in 10 s** → one message per channel per cooldown, grouped. Test: Task 3 `test_burst_is_grouped_per_cooldown`.
4. **Bot token leaking** into logs/API/errors (it is part of the Telegram URL). Test: Task 4 `test_token_redacted_in_logs_and_errors`, Task 5 `test_info_never_contains_secrets`.
5. **HDMI capture card unplugged and replugged** → `"reconnecting"` then back to `"running"` without restarting the engine. Test: Task 1 `test_capture_reopens_after_device_loss`.

---

### Task 1: Live sources — stream (PyAV), go2rtc, capture reconnect

**Files:**
- Create: `engine/sources/stream.py`, `engine/sources/backoff.py`
- Modify: `engine/sources/__init__.py`, `engine/sources/capture.py`, `engine/sources/file.py` (add `state`), `engine/core.py` (Protocol), `engine/config.py` (SourceConfig)
- Test: `tests/test_sources_live.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `VideoSource` Protocol gains property `state: str` — `"running"` or `"reconnecting"` (FileSource always `"running"`).
  - `Backoff(initial: float = 1.0, maximum: float = 30.0)`: `next_delay() -> float` (1, 2, 4, …, 30, 30…), `reset()`.
  - `StreamSource(url: str, *, open_timeout_s: float = 10.0, read_timeout_s: float = 5.0, opener: Callable[[str, dict], Any] | None = None, clock: Callable[[], float] = time.perf_counter, sleep: Callable[[float], None] = time.sleep)`:
    - a background reader thread decodes with PyAV (`av.open(url, options=...)`) and keeps only the **latest** frame (BGR); `read()` returns it once (else `None`), never blocks > 50 ms;
    - low-latency options: `{"fflags": "nobuffer", "flags": "low_delay", "max_delay": "500000"}` plus `rtsp_transport=tcp` for `rtsp://`, and an I/O timeout (`timeout`/`rw_timeout` in µs as the protocol requires);
    - on open failure / read error / no frame for `read_timeout_s`: `state = "reconnecting"`, wait `Backoff.next_delay()`, reopen; on the first good frame `state = "running"`, backoff reset;
    - `fps` from the stream's average rate, fallback `DEFAULT_FPS`; `ts` from `clock()` relative to open (monotonic across reconnects); `ended` is always False; `close()` stops the thread within 2 s.
    - `open()` does not raise for an unreachable stream (it starts in `"reconnecting"`), but raises `RuntimeError` for a malformed URL (no scheme / unsupported scheme).
  - `CaptureSource`: timestamps from `time.perf_counter()`; after `read()` fails continuously for 2 s → `state = "reconnecting"`, release and reopen the device with `Backoff`; `"running"` again on the first good frame.
  - `SourceConfig.type: Literal["file", "capture", "stream", "go2rtc"]`, new fields `url: str | None = None` (stream), `go2rtc: Go2rtcConfig` (`rtsp_base: str = "rtsp://127.0.0.1:8554"`, `name: str | None = None`); validation: `stream` requires `url` with scheme in `rtsp, rtsps, rtmp, rtmps, srt, udp, http, https`; `go2rtc` requires `go2rtc.name`. `build_source`: `go2rtc` → `StreamSource(f"{rtsp_base}/{name}")`.

- [ ] **Step 1: Write failing tests** (fake opener yields scripted frames/errors; fake clock/sleep; no network)

```python
def test_backoff_sequence()                       # 1,2,4,8,16,30,30; reset → 1
def test_stream_returns_latest_frame_only()       # opener yields 5 frames quickly → read() returns the 5th, then None
def test_stream_reconnects_with_backoff()         # opener raises twice then works → state reconnecting, sleeps [1, 2], then running
def test_stream_read_timeout_triggers_reconnect() # frames stop for > read_timeout_s → reconnecting, reopened
def test_stream_rtsp_options()                    # rtsp:// → options include rtsp_transport=tcp and nobuffer/low_delay
def test_stream_bad_url_raises()                  # "drone-feed" (no scheme) → RuntimeError
def test_stream_close_stops_thread()              # close() returns < 2 s, thread not alive
def test_stream_decodes_real_file()               # opener=None, url = local mp4 path from make_video → frames with BGR shape
def test_capture_reopens_after_device_loss()      # fake cv2 capture: frames, then 2.5 s of failures, then frames → reconnecting then running, reopened once
def test_source_config_stream_and_go2rtc()        # validation errors name source.url / source.go2rtc.name; go2rtc builds rtsp://127.0.0.1:8554/<name>
```

- [ ] **Step 2: Run** `python -m uv run pytest tests/test_sources_live.py tests/test_config.py -v` — FAIL. **Step 3:** Implement. **Step 4:** same + `python -m uv run pytest -q` — pass.
- [ ] **Step 5: Commit** — `feat(sources): RTSP/RTMP/SRT stream source, go2rtc, capture reconnect`

---

### Task 2: Pipeline + records — reconnecting state, `clip_status`, live `last_seen`

**Files:**
- Modify: `engine/pipeline.py`, `engine/storage/repository.py`, `engine/events/manager.py`, `engine/events/evidence.py` (failure/skip callbacks), `engine/api/app.py` (status passthrough only if needed)
- Test: `tests/test_pipeline.py`, `tests/test_storage.py`, `tests/test_events.py`

**Interfaces:**
- Produces:
  - `PipelineStatus.source_state` adds `"reconnecting"` (taken from `source.state` while the capture thread runs; `"ended"`/`"error"` rules unchanged).
  - `TruckRecord.clip_status: str = "pending"` — values `"pending" | "saved" | "skipped" | "failed" | "removed"`; column added by an idempotent migration on `TruckRepository` open (`ALTER TABLE trucks ADD COLUMN clip_status TEXT NOT NULL DEFAULT 'pending'`; existing rows with `clip_path` set → `"saved"`, else `"failed"`); `clear_clip_paths` sets `"removed"`.
  - `EvidenceRecorder.start_clip(..., on_done, on_fail: Callable[[str], None] | None = None)` — `on_fail("skipped")` when the cap skips a clip, `on_fail("failed")` on an encode error or abort; EventManager stores the status and publishes `truck.updated`.
  - `truck_to_json` adds `"clip_status"`.
  - Active trucks: EventManager writes `last_seen` for each active truck at most every **5 s** and publishes `truck.updated` for it (after releasing its lock).

- [ ] **Step 1: Write failing tests**

```python
def test_pipeline_reports_reconnecting()          # fake source state flips to reconnecting → status().source_state == "reconnecting", then "running"
def test_clip_status_migration(tmp_path)          # old-schema DB (no column) with one clipped + one unclipped row → opens, statuses saved/failed
def test_clip_status_lifecycle(tmp_path)          # new truck → pending; clip done → saved; cap skip → skipped; encode error → failed; retention → removed; each publishes truck.updated with clip_status
def test_last_seen_heartbeat(tmp_path)            # truck confirmed every frame for 12 s of frame time → ≥ 2 heartbeat updates, last_seen advances, ≤ 1 per 5 s
```

- [ ] **Step 2: Run** `python -m uv run pytest tests/test_pipeline.py tests/test_storage.py tests/test_events.py -v` — FAIL. **Step 3:** Implement. **Step 4:** `python -m uv run pytest -q` — pass.
- [ ] **Step 5: Commit** — `feat(events): reconnecting state, clip_status, live last_seen`

---

### Task 3: Alert core — dispatcher, cooldown/grouping, retries

**Files:**
- Create: `engine/alerts/__init__.py`, `engine/alerts/core.py`
- Test: `tests/test_alerts_core.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) AlertBatch(trucks: tuple[dict, ...], is_test: bool = False)` — truck dicts are `truck_to_json` output; `title() -> str` ("New truck #12" | "3 new trucks" | "Test alert from sky-eye").
  - `class Notifier(Protocol)`: `name: str` (`"desktop" | "telegram" | "mqtt"`); `send(batch: AlertBatch) -> None` (may raise; the dispatcher handles it).
  - `AlertDispatcher(bus: EventBus, notifiers: list[Notifier], enabled: Callable[[str], bool], cooldown_s: float = 10.0, retries: tuple[float, ...] = (1.0, 2.0, 4.0), queue_max: int = 100, clock=time.monotonic, sleep=time.sleep)`:
    - `start()` subscribes to the bus and starts one worker thread; listens only to `truck.new`;
    - per channel: if `clock() - last_sent >= cooldown_s` send now, else hold trucks until the window ends and send them as one batch;
    - disabled channels (`enabled(name)` False at send time) skip their batch;
    - `send_test() -> None` sends `AlertBatch((), is_test=True)` to every enabled channel immediately (ignores cooldown);
    - each send: up to 1 + len(retries) attempts with the listed sleeps; final failure → ERROR log naming the channel (no secrets), batch dropped;
    - `stop(timeout: float = 5.0)` unsubscribes, stops the worker, abandons pending retries.
- [ ] **Step 1: Write failing tests** (fake notifiers recording batches; fake clock/sleep)

```python
def test_first_truck_sent_immediately()
def test_burst_is_grouped_per_cooldown()          # 20 truck.new within 10 s → channel gets batch of 1 then one batch of 19 after the window
def test_cooldown_is_per_channel()                # desktop disabled for a while doesn't delay telegram
def test_disabled_channel_skipped()
def test_retries_then_drops(caplog)               # notifier raises 4× → 4 attempts, sleeps [1,2,4], ERROR logged, next truck still delivered
def test_failing_channel_never_blocks_publish()   # notifier blocks 10 s; bus.publish of 50 events returns in < 50 ms total
def test_queue_full_drops_oldest(caplog)          # queue_max 3, worker stalled, 5 events → oldest dropped, WARNING
def test_send_test_ignores_cooldown()
def test_stop_is_prompt()                         # stop() < 1 s while a retry sleep is pending
```

- [ ] **Step 2: Run** `python -m uv run pytest tests/test_alerts_core.py -v` — FAIL. **Step 3:** Implement. **Step 4:** pass + full suite.
- [ ] **Step 5: Commit** — `feat(alerts): dispatcher with per-channel cooldown, grouping and retries`

---

### Task 4: Notifiers — desktop, Telegram, MQTT

**Files:**
- Create: `engine/alerts/desktop.py`, `engine/alerts/telegram.py`, `engine/alerts/mqtt.py`
- Modify: `pyproject.toml` (`plyer~=2.1`, `paho-mqtt~=2.2`, `httpx~=0.28` moved to runtime deps), `uv.lock`
- Test: `tests/test_alerts_channels.py`

**Interfaces:**
- Consumes: `AlertBatch`, `Notifier` (Task 3).
- Produces:
  - `DesktopNotifier(notify=plyer.notification.notify, beep=<winsound.MessageBeep on Windows else terminal bell>)` — title from `batch.title()`, message "Seen at HH:MM:SS, score 82%" (local time; for groups "Latest at HH:MM:SS"); exceptions from plyer propagate (dispatcher retries).
  - `TelegramNotifier(token: str, chat_id: str, media_root: Path, dashboard_url: str | None = None, client: httpx.Client | None = None)`:
    - single truck with a crop on disk → `POST https://api.telegram.org/bot<token>/sendPhoto` (multipart `chat_id`, `caption`, `photo` = crop JPEG bytes read from `media_root / <path from crop_url without query>`); groups or missing crop → `sendMessage`; test batch → `sendMessage("Test alert from sky-eye ✅")`;
    - caption: title, local time, score, and `"<dashboard_url>/#/trucks/<id>"` when `dashboard_url` is set;
    - non-2xx or `{"ok": false}` → raise `AlertError` whose message and `__str__` never contain the token (redact `bot<token>` → `bot***` in any httpx error text); timeout 10 s.
  - `MqttNotifier(host: str, port: int = 1883, topic: str = "sky-eye/events", username: str | None = None, password: str | None = None, tls: bool = False, client_factory=paho.mqtt.client.Client)` — lazy connect + `loop_start()` on first send; publishes one JSON message per truck: `{"api":1,"type":"truck.new","truck":{...}}` (test batch: `{"api":1,"type":"alert.test"}`), QoS 1; publish failure / not connected → raise; `close()` disconnects.
  - `class AlertError(Exception)`.
- [ ] **Step 1: Write failing tests** (httpx `MockTransport`; fake paho client; fake notify/beep)

```python
def test_desktop_title_and_message()
def test_telegram_single_truck_sends_photo(tmp_path)    # multipart has chat_id, caption with link, photo bytes == crop file
def test_telegram_group_sends_message()
def test_telegram_missing_crop_falls_back_to_message(tmp_path)
def test_token_redacted_in_logs_and_errors(caplog)      # transport raises ConnectError whose text contains the full URL → AlertError text has "bot***", never the token
def test_telegram_not_ok_raises()                       # {"ok": false, "description": "..."} → AlertError
def test_mqtt_lazy_connect_and_publish()                # first send connects once, publishes JSON per truck on topic, qos 1
def test_mqtt_publish_failure_raises()
```

- [ ] **Step 2: Run** `python -m uv run pytest tests/test_alerts_channels.py -v` — FAIL. **Step 3:** Implement (`python -m uv add`/edit pyproject + `python -m uv sync`). **Step 4:** pass + full suite.
- [ ] **Step 5: Commit** — `feat(alerts): desktop, Telegram and MQTT notifiers`

---

### Task 5: Config, runtime switches, API, wiring

**Files:**
- Modify: `engine/config.py`, `engine/pipeline.py` (RuntimeSettings), `engine/app.py`, `engine/api/app.py`, `config.example.yaml`, `.env.example`
- Test: `tests/test_config.py`, `tests/test_api.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `AlertsConfig(cooldown_s: float = 10 (>0), desktop: bool = True, telegram: TelegramConfig(enabled: bool = False, dashboard_url: str | None = None), mqtt: MqttConfig(enabled: bool = False, host: str = "127.0.0.1", port: int = 1883 (1–65535), topic: str = "sky-eye/events", username: str | None = None, tls: bool = False))` on `AppConfig.alerts`. `telegram.enabled` requires env `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (ConfigError naming the missing variable).
  - `RuntimeSettings.alerts: dict[str, bool]` — keys exactly the **configured** channels (`desktop` if `alerts.desktop`, `telegram`/`mqtt` if enabled in config), initial value True; PUT may only toggle existing keys (unknown key → 422).
  - `build_engine` builds the notifiers + `AlertDispatcher(enabled=lambda n: runtime.alerts.get(n, False))`, starts it with the pipeline, stops it in `shutdown_engine` before the repo closes.
  - `POST /api/alerts/test` → `{"api":1,"sent":["desktop",...]}` (channels currently enabled); requires `Content-Type: application/json` like the runtime PUT.
  - `/api/info` gains `"alerts": ["desktop","telegram","mqtt"]` (configured channels only) — **never** any token, chat id, host password.
- [ ] **Step 1: Write failing tests**

```python
def test_alerts_config_defaults_and_bounds()
def test_telegram_enabled_requires_env(monkeypatch)
def test_runtime_alert_toggles(client)             # GET shows configured channels; PUT {"alerts":{"desktop":false}} → stored; {"alerts":{"sms":true}} → 422
def test_alert_test_endpoint(client)               # POST /api/alerts/test → sent lists enabled channels; text/plain → 415
def test_info_never_contains_secrets(monkeypatch)  # env token/chat id/password values absent from /api/info and /api/config/runtime bodies
def test_build_engine_starts_and_stops_dispatcher(tmp_path, monkeypatch)
```

- [ ] **Step 2: Run** `python -m uv run pytest tests/test_config.py tests/test_api.py tests/test_cli.py -v` — FAIL. **Step 3:** Implement; update `config.example.yaml` (alerts section, commented Telegram/MQTT), `.env.example` (`TELEGRAM_BOT_TOKEN=`, `TELEGRAM_CHAT_ID=`, `MQTT_PASSWORD=`). **Step 4:** full suite pass.
- [ ] **Step 5: Commit** — `feat(alerts): config, runtime channel switches, test endpoint`

---

### Task 6: Dashboard — source health, alert switches, clip status

**Files:**
- Modify: `dashboard/src/api/types.ts`, `dashboard/src/api/client.ts`, `dashboard/src/format.ts`, `dashboard/src/live/LivePage.tsx`, `dashboard/src/status/StatusPage.tsx`, `dashboard/src/trucks/TruckDetail.tsx`
- Test: matching `*.test.ts(x)` files

**Interfaces:**
- Consumes: Task 2 `clip_status`, `"reconnecting"`; Task 5 `/api/info.alerts`, `runtime.alerts`, `POST /api/alerts/test`.
- Produces:
  - Types: `SourceState` adds `"reconnecting"`; `Truck.clip_status?: "pending" | "saved" | "skipped" | "failed" | "removed"`; `Runtime.alerts?: Record<string, boolean>`; `Info.alerts?: string[]`.
  - `ApiClient.sendTestAlert(): Promise<string[]>` (POST JSON `{}`).
  - `SOURCE_LABEL.reconnecting = "Reconnecting"`; Live banner (warn) "Drone video lost — reconnecting…" while `source_state === "reconnecting"`.
  - Status page **Alerts** section: one switch per channel in `info.alerts` (labels Laptop / Telegram / MQTT; `role="switch"` or checkbox with label), saved immediately via `putRuntime({ alerts: {...} })` with revert on error; "Send test alert" button → shows "Sent to: Laptop, Telegram" or the error. Hidden when `info.alerts` is empty/absent.
  - Truck detail: when `clip_status` is present use it — `pending` → "Clip is being saved…" (keep 3 s refresh), `saved` → player, `skipped` → "No clip: too many trucks at once", `failed` → "Clip could not be saved", `removed` → "Clip removed to free disk space"; fall back to the Plan 2 heuristic only when `clip_status` is absent.
- [ ] **Step 1: Write failing tests**

```ts
test("reconnecting banner and label", ...)
test("alert switches render only configured channels and save", ...)   // info.alerts ["desktop","telegram"] → 2 switches; toggle telegram → putRuntime({alerts:{desktop:true,telegram:false}})
test("alert switch reverts on error", ...)
test("send test alert shows channels", ...)
test("clip status messages", ...)                                      // each status → expected text / player
```

- [ ] **Step 2: Run** `cd dashboard && npm test` — FAIL. **Step 3:** Implement. **Step 4:** `npm test`, `npm run typecheck`, `npm run build` — pass.
- [ ] **Step 5: Commit** — `feat(dashboard): source health, alert switches, clip status`

---

### Task 7: Docs and end-to-end check

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-11-05-sky-eye-phase1-design.md` (§5.1/§5.6 note what shipped), `tests/test_dashboard_e2e.py` (or new `tests/test_alerts_e2e.py`, marker `dashboard`)

- [ ] **Step 1: README sections**
  - **Live drone input:** HDMI capture card (`type: capture`, `device: 0`), DJI app RTMP → a local RTMP server/go2rtc (`type: go2rtc`, name), RTSP cameras/PX4 companions (`type: stream`, `url: rtsp://...`), what "Reconnecting" means.
  - **Alerts:** laptop alerts (on by default), Telegram (BotFather → token, get chat id, put both in `.env`, `alerts.telegram.enabled: true`, optional `dashboard_url` = the printed LAN URL), MQTT (broker host/port/topic, password in `.env`), the Status page switches and "Send test alert".
- [ ] **Step 2: E2E test** (`-m dashboard`): engine with FakeDetector + a recording fake notifier injected via monkeypatch; one truck → the fake notifier receives exactly one batch with that truck id; `POST /api/alerts/test` → fake notifier gets a test batch; shutdown < 5 s.
- [ ] **Step 3: Run** `python -m uv run pytest -q`, `python -m uv run pytest -m dashboard -v` (after `npm run build`), `cd dashboard && npm test` — all pass.
- [ ] **Step 4: Commit** — `docs: live drone input and alerts; e2e alert check`
