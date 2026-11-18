"""Contact sheets for labelling: crops of each candidate reference track, plus a
timeline, plus a truth-file template."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from engine.evaluation.cache import DetectionCache
from engine.evaluation.reference import (
    REF_FLOOR,
    REF_LOST_TIMEOUT_S,
    RefTrack,
    candidates,
    reference_tracks,
    save_refs,
)

PER_SHEET = 4
CROPS_PER_ROW = 6
CROP_H = 128
CROP_MAX_W = 192
EXPAND = 1.5
LABEL_H = 28
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def label_text(ref: RefTrack) -> str:
    votes = " / ".join(
        f"{name} {n}"
        for name, n in sorted(ref.class_votes.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    t = ref.track
    return (
        f"{ref.ref_id}  t={t.first_ts:.1f}–{t.last_ts:.1f} s  {votes}  "
        f"best truck {ref.best_truck_score:.2f}"
    )


def _ascii(text: str) -> str:
    return text.replace("–", "-")  # Hershey fonts have no en dash


def _pick(ref: RefTrack, n: int = CROPS_PER_ROW) -> list:
    boxes = ref.track.boxes
    if len(boxes) <= n:
        return list(boxes)
    idx = np.linspace(0, len(boxes) - 1, n).round().astype(int)
    return [boxes[i] for i in idx]


def _crop(image: np.ndarray, box) -> np.ndarray:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    bw, bh = (x2 - x1) * EXPAND, (y2 - y1) * EXPAND
    ix1, iy1 = max(int(round(cx - bw / 2)), 0), max(int(round(cy - bh / 2)), 0)
    ix2, iy2 = min(int(round(cx + bw / 2)), w), min(int(round(cy + bh / 2)), h)
    if ix2 <= ix1 or iy2 <= iy1:
        return np.zeros((CROP_H, CROP_H, 3), dtype=np.uint8)
    patch = image[iy1:iy2, ix1:ix2]
    scale = CROP_H / patch.shape[0]
    new_w = max(int(round(patch.shape[1] * scale)), 1)
    out = cv2.resize(patch, (new_w, CROP_H), interpolation=cv2.INTER_CUBIC)
    if new_w > CROP_MAX_W:  # very wide boxes: shrink to the cell, keep aspect
        k = CROP_MAX_W / new_w
        out = cv2.resize(out, (CROP_MAX_W, max(int(round(CROP_H * k)), 1)))
    return out


def _grab_frames(video_path: Path, cache: DetectionCache, wanted_ts: set) -> dict:
    """Decode the video once, sequentially, keeping only the needed frames (by ts)."""
    ts_to_index = {cf.ts: cf.index for cf in cache.frames}
    need = {ts_to_index[ts] for ts in wanted_ts if ts in ts_to_index}
    frames: dict[int, np.ndarray] = {}
    if not need:
        return {}
    last = max(need)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"cannot open video {video_path}")
    try:
        i = 0
        while i <= last:
            if not cap.grab():
                break
            if i in need:
                ok, img = cap.retrieve()
                if ok:
                    frames[i] = img
            i += 1
    finally:
        cap.release()
    return {ts: frames[ts_to_index[ts]] for ts in wanted_ts if ts_to_index.get(ts) in frames}


def _row(ref: RefTrack, by_ts: dict, width: int) -> np.ndarray:
    row = np.full((LABEL_H + CROP_H, width, 3), 30, dtype=np.uint8)
    cv2.putText(
        row, _ascii(label_text(ref)), (6, 19), _FONT, 0.55, (255, 255, 255), 1, cv2.LINE_AA
    )
    x = 0
    for ts, box in _pick(ref):
        image = by_ts.get(ts)
        if image is None:
            continue
        crop = _crop(image, box)
        h, w = crop.shape[:2]
        if x + w > width:
            break
        row[LABEL_H : LABEL_H + h, x : x + w] = crop
        x += w + 4
    return row


def _timeline(cands: list, duration: float) -> np.ndarray:
    left, right, bar_h, gap, top = 70, 150, 26, 10, 30
    plot_w = 1400
    width = left + plot_w + right
    height = top + len(cands) * (bar_h + gap) + 30
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    span = max(duration, 1e-6)

    def px(ts: float) -> int:
        return left + int(round(ts / span * plot_w))

    for sec in range(0, int(span) + 1, max(int(span // 10), 1)):
        x = px(sec)
        cv2.line(img, (x, top - 6), (x, height - 24), (225, 225, 225), 1)
        cv2.putText(img, f"{sec}s", (x - 8, height - 8), _FONT, 0.4, (90, 90, 90), 1, cv2.LINE_AA)
    for i, ref in enumerate(cands):
        y = top + i * (bar_h + gap)
        t = ref.track
        x1, x2 = px(t.first_ts), max(px(t.last_ts), px(t.first_ts) + 2)
        cv2.putText(img, ref.ref_id, (6, y + 18), _FONT, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
        cv2.rectangle(img, (x1, y), (x2, y + bar_h), (200, 160, 90), -1)
        cv2.rectangle(img, (x1, y), (x2, y + bar_h), (120, 90, 40), 1)
        first, last = t.boxes[0][1], t.boxes[-1][1]
        cx0 = int(round((first[0] + first[2]) / 2))
        cx1 = int(round((last[0] + last[2]) / 2))
        cv2.putText(
            img, f"x={cx0}", (max(x1 - 56, 4), y + 18), _FONT, 0.4, (0, 0, 0), 1, cv2.LINE_AA
        )
        cv2.putText(img, f"x={cx1}", (x2 + 4, y + 18), _FONT, 0.4, (0, 0, 0), 1, cv2.LINE_AA)
    return img


def sheets(
    cache: DetectionCache, video_path: Path, out_dir: Path, cache_name: str = ""
) -> tuple:
    """Write refs.json, sheet_NNN.png, timeline.png and truth_template.json into out_dir.
    Returns (number of refs, number of candidates, number of sheets)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    refs = reference_tracks(cache)
    cands = candidates(refs)
    save_refs(refs, out / "refs.json")

    wanted = {ts for r in cands for ts, _ in _pick(r)}
    by_ts = _grab_frames(Path(video_path), cache, wanted)

    width = CROPS_PER_ROW * (CROP_MAX_W + 4)
    n_sheets = 0
    for start in range(0, len(cands), PER_SHEET):
        sep = np.zeros((6, width, 3), dtype=np.uint8)
        stacked = []
        for r in cands[start : start + PER_SHEET]:
            stacked += [_row(r, by_ts, width), sep]
        n_sheets += 1
        cv2.imwrite(str(out / f"sheet_{n_sheets:03d}.png"), np.vstack(stacked))

    duration = cache.frames[-1].ts if cache.frames else 0.0
    if cands:
        cv2.imwrite(str(out / "timeline.png"), _timeline(cands, duration))

    template = {
        "video": Path(video_path).name,
        "frames": len(cache.frames),
        "fps": cache.fps,
        "cache": cache_name,
        "reference": {"floor": REF_FLOOR, "lost_timeout_s": REF_LOST_TIMEOUT_S},
        "candidates": [r.ref_id for r in cands],
        "trucks": [],
        "not_truck": [],
        "unsure": [],
        "labelled_by": "",
        "labelled_on": "",
    }
    (out / "truth_template.json").write_text(json.dumps(template, indent=1), encoding="utf-8")
    return len(refs), len(cands), n_sheets
