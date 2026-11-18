"""The hand-labelled truth file: which reference tracks are the same real truck."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TruthTruck:
    id: str
    refs: list[str]
    note: str = ""


@dataclass
class Truth:
    video: str
    frames: int
    fps: float
    cache: str
    reference: dict
    candidates: list[str]
    trucks: list[TruthTruck] = field(default_factory=list)
    not_truck: list[str] = field(default_factory=list)
    unsure: list[str] = field(default_factory=list)
    labelled_by: str = ""
    labelled_on: str = ""


def load_truth(path: Path) -> Truth:
    """Load and validate a truth file. Every candidate must appear exactly once
    across trucks[].refs, not_truck and unsure; ValueError names the problem."""
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    trucks = [
        TruthTruck(str(t["id"]), [str(r) for r in t.get("refs", [])], str(t.get("note", "")))
        for t in raw.get("trucks", [])
    ]
    truth = Truth(
        video=str(raw.get("video", "")),
        frames=int(raw.get("frames", 0)),
        fps=float(raw.get("fps", 0.0)),
        cache=str(raw.get("cache", "")),
        reference=dict(raw.get("reference", {})),
        candidates=[str(c) for c in raw.get("candidates", [])],
        trucks=trucks,
        not_truck=[str(r) for r in raw.get("not_truck", [])],
        unsure=[str(r) for r in raw.get("unsure", [])],
        labelled_by=str(raw.get("labelled_by", "")),
        labelled_on=str(raw.get("labelled_on", "")),
    )
    for t in truth.trucks:
        if not t.refs:
            raise ValueError(f"{p.name}: truck {t.id} has an empty refs list")
    known = set(truth.candidates)
    seen: set[str] = set()
    placed = [r for t in truth.trucks for r in t.refs] + truth.not_truck + truth.unsure
    for ref in placed:
        if ref not in known:
            raise ValueError(f"{p.name}: ref {ref} is not in candidates")
        if ref in seen:
            raise ValueError(f"{p.name}: ref {ref} is listed twice")
        seen.add(ref)
    missing = [c for c in truth.candidates if c not in seen]
    if missing:
        raise ValueError(f"{p.name}: candidate(s) not labelled: {', '.join(missing)}")
    return truth
