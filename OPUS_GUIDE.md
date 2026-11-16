# sky-eye — working guide for Opus 5.5

Written on 10 Nov 2026 by the previous session (Fable 5.2), for the Claude model that takes over this project and for the owner, Shobhit.

**Read this whole file before doing anything.** It tells you what the project is, how it is built, what state it is in today, what is wrong with it, and what to do next, in order.

The owner's words on 10 Nov 2026: *the tracking that was built is not good enough, and I want to get this project running and make a business from it.* That is the goal. Everything in this guide is ordered to serve it.

---

## 1. The job in one page

**sky-eye** watches drone video and finds trucks. For each truck it saves a photo, a close-up, a short clip, the time and (soon) a map location. It sends alerts and shows everything on a website (the dashboard).

**What works today:** the full chain runs on the owner's laptop (RTX 4050): video in → detect → track → save evidence → alerts → dashboard.

**What does not work well enough:** tracking. One real truck is saved as several different trucks, and junk gets saved as trucks. A customer cannot trust the count. Section 6 has the evidence and the fix plan.

**The order of work** (details in section 7):

| Step | What | Why now |
|---|---|---|
| 1 | Finish Plan 5 (GPS + map): it is about 90% done | Close the open branch before starting new work |
| 2 | **Tracking you can trust** (new Plan 4A) | The owner's main complaint; nothing can be sold without it |
| 3 | Train the detector on aerial footage (Plan 4) | The biggest single gain in accuracy |
| 4 | Speed and proof (Plan 6): steady frame rate, written results | Tracking needs a steady frame rate; customers need numbers |
| 5 | Field test with a real drone | Nothing has been tested on a real live drone link yet |
| 6 | Security fix, then the online dashboard (Tailscale + logins) | Needed before anyone outside the laptop uses it |
| 7 | Report export, public website, first pilot customer | The business part |

Do not start step N+1 while step N is unfinished, unless the owner says so.

---

## 2. The owner and how to work with him

- **Shobhit** owns the project and decides. GitHub account: `CRYPT-hack`. Repo: `CRYPT-hack/sky-eye` (private).
- He is new to video-streaming and drone terms. **Explain in plain words.** Say what a term means the first time you use it.
- He likes short answers. Lead with the result, then the next step.
- He wants **subagent-driven work** and **every finished step pushed to GitHub** on a feature branch.
- **He merges pull requests himself**, unless he tells you to merge.
- Ask him before anything that costs money, publishes something, or cannot be undone.
- He is in India (time zone Asia/Kolkata).

Decisions he already made (do not re-ask):

- One class, `truck`, covering every kind of truck. Other vehicles come later.
- Phase 1 runs on a ground laptop. Later phases: a Jetson on the drone, satellite images, other vehicle classes.
- It must work with any drone.
- Online dashboard: reached through **Tailscale** (nothing public), with **user accounts, hashed passwords, admin and viewer roles**.
- Public project website: on a **Vercel free subdomain**, using **freely licensed drone footage only**.

---

## 3. Hard rules

Break none of these.

1. **Secrets** live only in `.env` or the environment: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `MQTT_PASSWORD`, `SKYEYE_API_TOKEN`, stream passwords, MAVLink connection strings. Never log them, return them from the API, or store them in the database or config.
2. **Never publish or commit the clips in `samples/`.** They carry a "DVIDEO" stock watermark. Use them only on this laptop.
3. **Never stage** `data/`, `config.yaml`, `.env`, `samples/`, `graphify-out/`, `.graphify_python`, `dashboard/dist`. Use `git add <exact files>`, never `git add .`.
4. **Licences:** the project is Apache-2.0. No AGPL code (so no Ultralytics YOLO in the default install). Check the licence of every new package, model and dataset before using it. See section 9 for the dataset warning.
5. **Detection never waits** on alerts, telemetry, slow browsers or the disk. Queues are bounded; slow things drop work.
6. **Shutdown is bounded:** pipeline 5 s, alerts 3 s, database always closed.
7. **Tests:** never use port 8000; anything with sockets or threads has a hard timeout; run one test suite at a time (parallel runs gave false failures).
8. Never work on `main`. One feature branch per plan.
9. Report honestly. If a test fails or a step was skipped, say so.

---

## 4. How the system is built

### 4.1 The flow

```
video source ──► detector ──► tracker ──► event manager ──► database (SQLite)
(file, camera,   (RF-DETR)    (ByteTrack)  │        │
 RTSP/RTMP/UDP,                            │        └─► evidence recorder (photo, crop, clip)
 go2rtc)                                   │
                                           ▼
telemetry ──► geo (lat/lon + error)     event bus ──► alert dispatcher ──► laptop popup, Telegram, MQTT
(DJI .SRT, MAVLink)                        │
                                           └─► API (REST + WebSocket + MJPEG) ──► dashboard website
```

- A **capture thread** reads frames. An **inference thread** runs detect → track → events. In live mode old frames are dropped so the picture stays current ("live beats complete").
- `build_engine()` in `engine/app.py` builds every part and connects them. Start reading there.
- The **event bus** is the connection point: the event manager publishes `truck.new` and `truck.updated`; the alert dispatcher and each browser's WebSocket listen.

### 4.2 Where the code is

| Path | What it holds |
|---|---|
| `engine/app.py` | Start-up, `build_engine`, shutdown, the command line |
| `engine/config.py` | The config file's shape and checks (pydantic) |
| `engine/core.py` | Shared types: `Frame`, `Detection`, `Track`, the `VideoSource` / `Detector` / `Tracker` contracts |
| `engine/pipeline.py` | The two threads, status, the live picture |
| `engine/sources/` | File, capture card, network stream, reconnect with backoff |
| `engine/detectors/rfdetr.py` | The detector (RF-DETR Small, COCO-pretrained) |
| `engine/tracking/bytetrack.py` | The tracker wrapper: **the weak part** |
| `engine/events/` | Event manager, evidence recorder, event bus |
| `engine/storage/` | SQLite repository, disk-space retention |
| `engine/alerts/` | Dispatcher and the three notifiers |
| `engine/telemetry/`, `engine/geo.py` | Drone position and truck location (Plan 5) |
| `engine/api/` | FastAPI app, auth |
| `dashboard/` | React 19 + TypeScript + Vite website: Live, Trucks, Map, Status |
| `tests/` | Engine tests (pytest). `tests/test_dashboard_e2e.py` runs the real engine with the built site |
| `docs/superpowers/specs/` | **The spec**: the binding description of Phase 1 |
| `docs/superpowers/plans/` | One plan per piece of work |
| `docs/superpowers/plan*-followups.md` | What each plan left open on purpose |
| `graphify-out/graph.html` | A knowledge graph of the code (optional aid; not committed) |

### 4.3 Commands (Windows 11, Git Bash or PowerShell)

```bash
# Python always through uv ("uv" alone is not on PATH)
python -m uv sync
python -m uv run python -m engine --config config.yaml          # run
python -m uv run python -m engine --config config.yaml --check  # check the setup only
python -m uv run pytest -q -p no:cacheprovider                  # engine tests (~1-2 min)
python -m uv run pytest -m dashboard -v -p no:cacheprovider     # end-to-end (needs dashboard/dist)

cd dashboard
npm ci && npm run build      # build the site (the engine serves dashboard/dist)
npm test                     # dashboard tests
npm run typecheck
```

- `gh` has two accounts. `CRYPT-hack` must be the active one to push (`gh auth status`).
- The dashboard opens at `http://localhost:8000/` when the engine runs with the default config.
- Logs: `data/logs/sky-eye.log`. Database: `data/sky-eye.db`.

---

## 5. Where things stand (10 Nov 2026)

### 5.1 Status board

Update this table at the end of every working session.

| Piece | State |
|---|---|
| Plan 1: engine core | Merged |
| Plan 2: dashboard (Live, Trucks, Status) | Merged (PR #5) |
| Plan 3: alerts + live drone input | Merged (PRs #7, #8) |
| Plan 5: GPS + map | **Done, PR open** (17 Nov): branch `plan5-gps-map`, final review fixed and re-reviewed; waiting for the owner to merge. Leftovers in `plan5-followups.md` |
| Plan 4A: tracking you can trust | **Not started. Next after Plan 5. Top priority.** |
| Plan 4: train the detector on aerial footage | Not started |
| Plan 6: speed, options, results file | Not started |
| Online dashboard (Tailscale + logins) | Not started; decisions made (section 2) |
| Public website (Vercel) | Not started; decisions made (section 2) |
| Report export for customers | Not planned yet; ask the owner (section 9) |

### 5.2 The spec's "Phase 1 is done when…" list, checked honestly

| # | Criterion (spec §8) | Today |
|---|---|---|
| 1 | Runs end to end on a recorded clip and on one live input | Recorded clip: yes. Live input: built and tested with fakes only; **never tried with a real camera or drone** |
| 2 | 15 frames per second or more on the RTX 4050 | **Not proven.** Seen: 12–23 with the dashboard open, dropping to 3–5 when the laptop is busy |
| 3 | A fine-tuned model beats the stock model, numbers in `docs/results.md` | Not started |
| 4 | Each truck is saved once (duplicates ≤ 10%) | **Failing.** See section 6 |
| 5 | Dashboard works on a phone on the same Wi-Fi | Checked at phone width in the app's browser only, not on a real phone |
| 6 | Laptop and Telegram alerts within 3 s | Laptop popups work. **Telegram has never been tried with a real bot** |
| 7 | Trucks appear on the map with an error radius | Plan 5; passes its tests; not yet seen with a real flight log |
| 8 | All tests pass | See 5.3 for the latest run |

### 5.3 How Plan 5 was finished (history; done on 17 Nov 2026)

On 17 Nov the remaining README and config items were finished. A scoped re-review approved all 16 fix items. All suites passed: engine 474, dashboard 220, typecheck, build, and the end-to-end test 3 times in a row. The pull request was opened. **Your first job is now Step 2 (section 7), after the owner merges the Plan 5 PR.** The notes below are kept for the record.

State on disk:

- Branch `plan5-gps-map`. GitHub has it up to commit `1cfb098`. Five fix commits after that (`ca05780`, `564039d`, `bd358f7`, `30bda78`, `b05ce62`) were local when this guide was written; this guide's own commit pushed them.
- The fix agent was cut off twice by the usage limit and **never wrote its report or ran the final checks**. It cannot be resumed from a new session.
- Untracked files: `docs/superpowers/plan5-followups.md` is a finished draft; commit it at the end. Never commit `graphify-out/` or `.graphify_python`.
- The working notes are in `.superpowers/sdd/2026-11-07-plan5-gps-map/` (git-ignored, only on this laptop):
  - `progress.md`: the ledger, with every decision ("Ruling:" lines).
  - `final-findings.md`: **the fix list** from the final review (I1–I4, M1–M12, the test time-zone item).
  - `final-review.md`: the reviewer's full report.
- Test run on `b05ce62` (10 Nov): see the note at the end of this section.

Steps:

1. Read `final-findings.md`. Check **every** item against the code. Do not trust commit subjects.
2. A quick look on 10 Nov found these **done in code**: I1 (assumed camera angles → unreliable, `camera.pitch_deg`), I3 (more DJI log formats), I4's error hints, M3, M6, M7, M8, M9, the test time-zone pin.
3. The same look found these **missing**: the README changes (I1's warning that many DJI logs have no camera angles; I4's rewrite for a ground station on the same laptop, with port 14445; M6's note about tile keys) and the new keys in `config.example.yaml`. **Not checked:** M1, M4, M10, M11, I2's tests. Check them.
4. Finish what is missing with **one** fix subagent. Give it the list of open items only.
5. Run each suite once, one at a time: engine tests, `npm test`, `npm run typecheck`, `npm run build`, then the end-to-end test three times.
6. Run **one** scoped re-review of `1cfb098..HEAD` against `final-findings.md` (template: the `re-review-prompt.md` in the subagent-driven-development skill).
7. Commit `plan5-followups.md`. Push. Open a pull request to `main`.
8. In your message to the owner, list every "Ruling:" line from `progress.md` with what it costs if wrong. Then delete the workspace folder.
9. The owner merges.

Test run on `b05ce62`, 10 Nov 2026:

- Engine: **474 passed**, 4 deselected (the usual torch warning only).
- Dashboard: **220 passed**; typecheck clean.
- **Not run:** `npm run build` and the end-to-end test. Run them in step 5 above.

---

## 6. The main problem: tracking

### 6.1 What "tracking" means here

The **detector** looks at one picture and draws boxes. The **tracker** decides that the box in this picture is the same truck as the box in the last picture, and gives it one ID. One ID = one saved truck, one alert, one clip. If the tracker loses a truck and finds it again, it gives a new ID, and the same truck is saved twice.

### 6.2 The evidence

From the database on this laptop (`data/sky-eye.db`), all on `samples/drone3.mp4` (49.5 s, 1280×720, a highway seen from a drone):

| Run | Conditions | Trucks saved | Tracked for (middle value) | Under 2 s | Longest |
|---|---|---|---|---|---|
| 2 | Default settings, laptop idle (~29 frames per second) | 38 | 1.3 s | 73% | 6 s |
| 3 | Default settings, laptop busy (~3–5 frames per second) | 10 | 0.5 s | 90% | 2 s |
| 5 | Default settings, laptop busy | 20 | 0.0 s | 95% | 2 s |
| 6 | Demo on 7 Nov: video looping, confidence slider moved 14 times | 1,415 | 0.6 s | 81% | 8 s |

What this shows:

- Trucks on a highway stay in view for many seconds, yet most saved "trucks" were followed for under 2 seconds. **Tracks are breaking into pieces.**
- A slower frame rate makes it much worse.
- With the confidence slider turned down, the system saved 1,415 "trucks" in a few minutes (middle score 0.24). **Nothing stops junk from becoming a truck.**
- In the demo, truck #81 was a bulldozer, scored 78%.

**Nobody has counted how many trucks are really in the clip.** So the size of the error is unknown. Fixing that comes first.

### 6.3 Likely causes

These are ranked guesses with pointers. **Confirm each with measurements before fixing it.**

1. **Nothing measures tracking.** The tests use fake detectors. There is no labelled footage, no score, no check on real video. Every setting was chosen by eye.
2. **The detector was not trained for the view from above.** It is the stock RF-DETR Small model, trained on everyday photos (COCO). From a drone, trucks are small and seen from the top. Scores flicker around the cut-off, so the truck vanishes for a few frames.
3. **The picture is shrunk too much.** The clips are 1280×720; `detector.input_size` is 512. A 40-pixel truck becomes about 16 pixels.
4. **Only the label "truck" is kept** (`engine/detectors/rfdetr.py`, `detect()` drops every class not in `detector.classes`). The stock model often calls a truck "car" or "bus" for a few frames. Each of those frames is a gap.
5. **ByteTrack's second matching step is switched off.** `engine/tracking/bytetrack.py` sets `high_conf_det_threshold=0.0` and `track_activation_threshold=0.0`, and the detector cuts at the confidence setting before the tracker sees anything. ByteTrack is designed to use low-score boxes to keep an existing track alive; here it never gets them. And when the slider is lowered, every low-score box may start a new track. (`plan1-followups.md` line 18 already notes this.)
6. **The camera moves and the tracker does not know.** The drone flies and turns, so every box shifts between frames. The tracker predicts motion as if the camera stood still, and matches by box overlap only. Small boxes stop overlapping after a few pixels of shift.
7. **Uneven frame rate.** Live mode drops frames. On a busy laptop the gaps between processed frames reach 0.2–0.3 s. A small truck moves further than its own length in that time.
8. **A lost track is gone for good after 2 s** (`tracker.lost_timeout_s`). The truck then gets a new ID. Nothing joins the two pieces.
9. **A track becomes a "truck" too easily.** The rule is 3 hits in the last 5 processed frames, about a tenth of a second of evidence at 30 frames per second. There is no minimum time and no minimum best score.
10. **Looping a video counts the same trucks again** each pass. This only matters for demos.

Useful fact: the installed `trackers` package (2.7.0) already includes other trackers that the code does not use: `BoTSORTTracker` and `OCSORTTracker` (both can correct for camera motion: `enable_cmc`), and `CBIoUTracker` (matches with enlarged boxes, made for small or fast objects).

### 6.4 The fix plan ("Plan 4A: tracking you can trust")

Run it through the normal process (section 8): brainstorm with the owner, write a short spec addition, write a plan, execute with subagents. The steps below are the content of that plan.

**Step A. Build the measuring stick first.** No tuning before this exists.

1. Ask the owner to watch each sample clip and write down **how many different trucks appear**. Ten minutes of his time gives the first real number.
2. Ask the owner for **footage from the real job** (his drone, his height, his camera angle), if he has any. The system must be judged on that, not only on stock clips.
3. Write an offline evaluation tool (for example `tools/eval_tracking.py`):
   - It runs detector + tracker on a video file, **every frame, no dropping**, so results repeat exactly.
   - It **saves the raw detector output to disk** (all vehicle classes, a low cut-off such as 0.05). Tracker experiments then run in seconds without the GPU.
   - It writes the tracks in the standard MOT text format and prints: trucks saved, how long each was tracked, how many were under 1 s and under 2 s.
4. Make ground truth for the sample clips: boxes with IDs for every truck. CVAT (free, runs locally) has a track mode that fills in between key frames. The owner or you can correct machine-made first drafts. Keep the label files in the repo (they are text); keep the clips out.
5. Add scores against ground truth: **IDF1**, **HOTA** and **ID switches** (standard tracking scores; `TrackEval` or `py-motmetrics`, check their licences), plus four plain business numbers:
   - trucks counted vs trucks really there;
   - duplicates (extra IDs for a truck already counted);
   - missed trucks;
   - false trucks (things that are not trucks).
6. Record the starting numbers in `docs/results.md`.

**Step B. Fix the logic, one change at a time.** Measure each change. Keep it only if the numbers improve.

1. **Give ByteTrack its low-score boxes.** The detector returns everything above a low cut-off (about 0.1). The confidence setting becomes the tracker's "high" threshold; starting a new track needs a higher score. Low-score boxes may only continue a track that already exists. Check: lowering the slider to 0.2 must no longer flood the system.
2. **Track all vehicles, decide the label per track.** Feed car, bus and truck boxes to one tracker. Call a track a truck from its votes over time (for example: enough frames labelled truck, or a high enough best truck score). Measure false trucks carefully; this can turn buses into trucks if it is too loose.
3. **Make "truck" harder to earn.** Create the truck event only when the track has lasted a minimum time (try 0.5–1.0 s), has enough hits, and its best score passed the confidence setting.
4. **Correct for camera motion.** Try `BoTSORTTracker` and `OCSORTTracker` with `enable_cmc`, and `CBIoUTracker`. Pick by the scores. Add `tracker.type` to the config.
5. **Join broken pieces.** When a new track starts close to where a recently lost track was heading, with a similar size, treat it as the same truck. Try a longer lost time-out as well. Appearance matching (comparing how the truck looks) is a later option if motion alone is not enough.
6. **Bigger picture for the detector.** Test `input_size` 640–768 (the model accepts only certain sizes; the error message says which). Also test splitting a wide frame into two overlapping halves. Watch the frame rate.
7. **An accurate mode for recorded flights.** For files, `realtime: false` processes every frame. Document it as the mode for counting; live mode is for alerts.

**Targets for Step B, at default settings on the labelled clips:**

- duplicates ≤ 10% (the spec's criterion 4);
- missed trucks ≤ 10%;
- false trucks ≤ 10%;
- the slider at 0.2 does not multiply the count.

If the stock model cannot reach these, say so with the numbers and go to Step C. Do not hide it with tuning.

**Step C. Train the detector on aerial footage (Plan 4).** This is the largest gain for both detection and tracking.

- One class, `truck`; cars, buses and vans stay in the data as "not truck", so the model learns the difference (bulldozers and other machines too, if footage exists).
- The RTX 4050 (6 GB) is enough for small runs. Longer runs need a free Kaggle or Colab GPU; ask the owner before using any paid service.
- Read the dataset warning in section 9 **before** choosing data.
- Compare the trained model with the stock one on the same labelled clips and a held-out set. Write the numbers in `docs/results.md`. Swap it in only if it wins.
- `plan1-followups.md` notes that the class map is hard-coded for COCO; custom weights need their own class map.

**Step D. Steady speed (Plan 6).** Tracking quality depends on frame rate.

- Measure frames per second on an idle, plugged-in laptop first.
- Export the model to ONNX, then TensorRT if needed. Target: a steady 15 or more.
- Known waste, listed in `plan3-followups.md`: every frame is converted even when it is then dropped; the decoder does not use all threads.

**Step E. Counting for customers.** Only after tracking is stable, and only after asking the owner what the customer needs: unique trucks per hour, trucks crossing a line, trucks entering an area, direction of travel.

---

## 7. The roadmap, step by step

For each step: what to do, and when it is done.

**Step 1. Finish Plan 5.** See 5.3. *Done when:* the pull request is open, tests are green, the owner has the list of decisions.

**Step 2. Plan 4A: tracking.** Section 6.4, Steps A and B. *Done when:* the evaluation tool and labelled clips are in the repo, `docs/results.md` shows before and after, and the targets are met or the shortfall is stated with numbers.

**Step 3. Plan 4: training.** Section 6.4, Step C. *Done when:* a trained model beats the stock model on the same test and is the default.

**Step 4. Plan 6: speed and proof.** Section 6.4, Step D, plus a full check of the spec's eight criteria, written up in `docs/results.md`. *Done when:* each criterion is marked pass or fail with evidence.

**Step 5. Field test.** The owner does this with a real drone; you prepare a checklist and fix what breaks.

- A live stream reaches the laptop and survives a dropped link.
- Telegram alerts arrive on a real phone (the owner creates a bot and puts the token in `.env`).
- The dashboard works on a real phone on the same Wi-Fi.
- A real flight log puts trucks on the map in roughly the right place.

*Done when:* the owner has flown once and the problems found are fixed or listed.

**Step 6. Security fix, then the online dashboard.**

- First fix the known leak: the dashboard sends its login token in web addresses (`?token=`), and the engine's request log writes those addresses to the screen and `data/logs/sky-eye.log` (top item in `plan3-followups.md`). No token is configured today, so nothing has leaked yet.
- Also read the "before the online dashboard" list in `plan5-followups.md`.
- Then build what the owner chose: Tailscale access, user accounts with hashed passwords, admin and viewer roles. Brainstorm, spec and plan it properly; this is security work.

**Step 7. Business pieces.** Ask the owner before building any of these.

- **Report export:** a file a customer can keep: each truck with time, photo and location, as CSV and PDF. Customers usually pay for a report, not for a live screen.
- **Public website** on Vercel with a demo made from freely licensed footage.
- **Easy install:** one script that sets up Python, Node and the model on a Windows laptop.

**Later phases** (the owner asked that these not be forgotten): detection on the drone itself (Jetson), satellite images, other vehicle classes.

---

## 8. How to do the work

### 8.1 The process for each plan

Use the superpowers skills, in this order:

1. `brainstorming`: agree the goal and the open questions with the owner.
2. Write or extend the spec in `docs/superpowers/specs/`.
3. `writing-plans`: a plan file in `docs/superpowers/plans/`.
4. `subagent-driven-development`: one implementer per task, a review after each task, a final whole-branch review, one fix wave, one scoped re-review. Keep the ledger; it is what survives when your memory is cut.
5. `finishing-a-development-branch`: tests, push, pull request. The owner merges.
6. Write `docs/superpowers/plan<N>-followups.md` with what was left open.
7. Update the status board in this guide and the memory file.

### 8.2 What this project has taught so far

- **The usage limit is real.** Opus subagents hit the 5-hour limit three times and died mid-task. Use Sonnet for implementers and most reviews. Keep Opus for the final whole-branch review and the hardest tasks. After a limit, look at `git status` and the ledger before doing anything.
- **An agent's "done" is a claim.** Check `git log`, read the diff, run the tests yourself.
- **Reviews find real bugs.** Final reviews caught the Telegram token in the logs (Plan 3) and wrong locations shown as reliable (Plan 5). Never skip them.
- **Hand files to agents, not pasted text.** Briefs, findings and diffs go in the plan's workspace folder; the prompt carries the paths.
- **Record every decision you take for the owner** as a "Ruling" in the ledger with what it costs if wrong, and show him the list at the end.
- **Measure before tuning.** The tracker settings were picked without measurements. That is how section 6 happened.
- Small traps: `uv` is not on PATH (use `python -m uv`); jsdom is pinned to `^29.1.1` (newer needs Node 24.15); PyAV has no SRT support (relay through MediaMTX); the dashboard tests pin the time zone to Asia/Kolkata (`TEST_TZ` overrides it).

### 8.3 Showing the owner the website

Build the site (`cd dashboard && npm run build`), start the engine on a sample clip, open `http://localhost:8000/`. For a demo that keeps playing, set `source.loop: true` in a copy of the config, and bind to `127.0.0.1`. Remember that looping counts the same trucks again on each pass, and that the engine keeps saving evidence until it is stopped.

---

## 9. The business side

This section is for the owner as much as for you. It contains business notes; **if the repo is ever made public, move this section out first.**

### 9.1 What a customer pays for

A customer pays for a **number or an alert they can trust**: "37 trucks left the quarry today", or "a truck entered the closed road at 02:14, here is the photo and the place". The live video with boxes is the demo; the trusted count and the evidence are the product. That is why tracking comes before every new feature.

### 9.2 The smallest thing that can be sold

1. The truck count is within about 10% of the truth **on the customer's own footage**, shown with numbers.
2. It installs and runs on an ordinary gaming laptop without a developer.
3. It produces a report the customer can keep.
4. A short demo video on freely licensed footage, and a results page.

### 9.3 Questions only the owner can answer

Ask these at the start of Step 2. The answers change what gets built.

1. **Who is the first customer, and what do they need to know?** Possible fits: a road authority or traffic survey firm (counts), a quarry, mine or construction site (trucks in and out), a logistics yard or port, an agency watching for illegal mining or dumping. These are guesses, not research.
2. **Live or recorded?** Alerts during the flight, or a count after it? Recorded is easier and more accurate.
3. **Which drone, how high, what camera angle?** The training footage must look like the real job.
4. **Is there money for a cloud GPU or for labelling?** Free options exist but are slower.
5. **What is sold:** software, a counting service with reports, or a laptop kit?

### 9.4 Checks before taking money

These are flags to check, not legal advice.

- **Training data licences.** The datasets named in the spec (VisDrone, UAVDT) and those kept for later (DOTA, xView) were published for research. Several forbid commercial use. A model trained on them may not be usable in a product that is sold. **Check each licence before training.** The safest data is footage the owner films himself or has permission to use, plus datasets whose licence allows commercial use.
- **Model licence.** RF-DETR Nano, Small and Medium are Apache-2.0. Check again when changing model.
- **Video encoding.** The spec carries a note that clips are encoded with x264, which is GPL. That matters if the software is shipped to customers; check before packaging.
- **Map tiles.** The public OpenStreetMap tile server is for light use and gives no guarantee. A sold product needs a paid tile provider or its own tiles (`api.map_tile_url`).
- **Drone rules and privacy.** Commercial drone flying and filming people or vehicles are regulated (in India: the DGCA drone rules and the data protection law). The owner must check what applies to him and to each customer.
- **The sample clips** are watermarked stock footage and can never appear in a demo.

---

## 10. First session checklist

1. Read this file, then the spec (`docs/superpowers/specs/2026-11-05-sky-eye-phase1-design.md`) and the three follow-up files.
2. Run `git status`, `git log --oneline -8`, and `gh auth status`.
3. Run the engine tests and the dashboard tests once, to know the real state.
4. Tell the owner in a few lines where things stand and that you are starting Step 1.
5. Finish Plan 5 (5.3).
6. Ask the owner the questions in 9.3, then start Plan 4A.
