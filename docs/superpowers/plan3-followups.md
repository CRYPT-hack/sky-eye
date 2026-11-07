# Plan 3 (alerts + live drone input): follow-ups

Plan 3 is complete and reviewed, including a final whole-branch review and one fix wave. This file lists what was deliberately left open.

## Worth doing soon

- **Fix before the online dashboard: the API token reaches the access log.** Media, MJPEG and WebSocket URLs carry `?token=`, and uvicorn's access log writes the full query string to the console and `data/logs/sky-eye.log` (also in `plan1-followups.md`). The new `RedactingFilter` in `engine/logging_setup.py` only covers Telegram bot tokens; extend it to `token=` query values, or move the token to a cookie/header.
- **Telegram rate limits.** A burst of trucks can hit Telegram's `429 Too Many Requests`. The notifier retries at 1, 2 and 4 s but ignores the `retry_after` value Telegram sends, so a long limit drops the batch (`engine/alerts/telegram.py`).
- **WebSocket clients drop messages silently.** Each dashboard connection has its own 100-message `Subscription`; when a slow client falls behind, the oldest messages are dropped and counted, but nothing logs or reports the count (`engine/api/app.py` `/ws`). On a slow phone link (the planned Tailscale dashboard) the gallery can miss a `truck.new` until a refresh. Log the count, or have the client refetch after a gap.
- **Busy scenes skip many clips.** Only a few clips may collect or encode at once; further trucks get `clip_status: skipped` (snapshot and crop are still saved). Seen on `drone3.mp4`: most trucks in a busy stretch had no clip. Consider one shared clip for trucks that overlap in time, or a larger limit on a machine with spare CPU (`engine/events/evidence.py`).
- **The detector calls construction machines "trucks".** The COCO-pretrained RF-DETR labelled a bulldozer as a truck at 78%. Plan 4 (training on aerial data) is the fix.

## Engine items for later plans

| Plan | Item |
|---|---|
| 5 | The detail page's "On screen for" only updates on reload; a live duration over the WebSocket would keep it current |
| 6 | Decoding: PyAV `thread_type` is not set to AUTO, and every frame is converted to BGR even when it is then dropped |
| 6 | The 5 s `last_seen` heartbeat commits (and fsyncs) once per truck; batch the writes when many trucks are on screen |
| 6 | Measure FPS on an idle, plugged-in machine (12-23 FPS observed on 7 Nov with the dashboard open, dipping to ~4.5 under load) |

## Small residual fixes (any time)

- MQTT delivery is at-least-once: a batch that partly published can repeat on retry. Consumers should de-duplicate by truck id if it matters.
- A go2rtc stream name is put into the RTSP URL without URL-quoting; names with spaces or `/` will break.
- The `@`-after-host check rejects credential-free `http(s)` URLs that have `@` in the path or query.
- `StreamSource.url` returns the raw URL; any future caller that logs it must use `redact_url`.
- `CaptureSource` reopens the device on the capture thread, which blocks frame reads during the reopen.
- `_publish` reads the truck from the DB and publishes outside one lock, so a racing update can publish a stale `pending` last.
- A failed frame skips the improved/ended `truck.updated` publishes for that frame.
- `build_engine` does not close notifiers it already built when a later step fails.
- Desktop notifier (plyer on Windows) shows the balloon on its own thread; a failure goes to stderr and cannot be retried.
- `send_test` waits behind retries and can be evicted by drop-oldest on a full channel inbox; a held batch on a channel switched off is dropped without a log line; more than 100 trucks in one batch are announced as "100 new trucks".
- Alert channel internals: the drain list per step is unbounded; items drained in a step that raises are discarded without a count; restarting the dispatcher resets `last_sent`; inbox/pending overflow WARNINGs are not rate-limited.
- Telegram: no caption length guard; the photo is always sent as JPEG; `AlertError.__context__` still references the httpx exception.
- The redaction filter covers Telegram bot tokens only; stream credentials rely on `redact_url` at each call site.
- Shutdown: "dropped N held alert(s)" can be off by one in-flight batch when a channel worker outlives `stop()` (`engine/alerts/core.py` `_drop_held`); nothing is delivered after stop. With an MQTT send in flight, dispatcher stop (1.5 s) plus MQTT close (up to 2 s) can pass the 3 s budget; the main thread still returns at 3 s with a WARNING.
- `test_mqtt_close_does_not_wait_for_a_send_in_progress` asserts `close() < 2.0 s`, which is exactly the designed worst case (0.5 s lock + 1.5 s join). Measured close is ~0 s; give it headroom if it ever flakes.
- Dashboard: alert switch state is updated before the stale-response check; changing the token doesn't reset the alert sequence, errors or test result; the test-result live region is inserted together with its content.
- Test gaps: backoff growth on a frameless connect; close while the opener blocks; abort-twice with a cancelled future; migration reopen with pending rows; `alerts: null` PUT; lax bool coercion; symlink/backslash crop paths; a few micro-races in channel tests not seen to flake.
