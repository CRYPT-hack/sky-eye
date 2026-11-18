from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time
from collections import Counter
from pathlib import Path

from engine.config import AppConfig, ConfigError, load_config
from engine.evaluation.cache import build_cache, load_cache, save_cache
from engine.evaluation.replay import replay, summarize


def _cmd_cache(args: argparse.Namespace) -> int:
    from engine.detectors import RFDETRDetector

    classes = [c.strip() for c in args.classes.split(",") if c.strip()]
    video = Path(args.video)
    out = (
        Path(args.out)
        if args.out
        else Path("eval/cache") / f"{video.stem}__{args.model}_{args.size}.npz"
    )
    detector = RFDETRDetector(
        size=args.model,
        weights=None,
        input_size=args.size,
        confidence=args.floor,
        device=args.device,
        classes=classes,
    )
    start = time.perf_counter()
    cache = build_cache(
        video,
        detector,
        floor=args.floor,
        classes=classes,
        model=args.model,
        input_size=args.size,
        progress=lambda n: print(f"  {n} frames", flush=True),
    )
    seconds = time.perf_counter() - start
    save_cache(cache, out)
    counts = Counter(d.class_name for f in cache.frames for d in f.detections)
    print(f"frames={len(cache.frames)} seconds={seconds:.1f} detections={dict(counts)}")
    print(f"wrote {out}")
    return 0


def _load_config_lenient(path: Path) -> AppConfig:
    """load_config, but a bad source section (e.g. a video path that is not on
    this machine) is dropped: replay never opens the source."""
    try:
        return load_config(path)
    except ConfigError as exc:
        lines = str(exc).splitlines()
        if not lines or not all(line.startswith("source") for line in lines):
            raise
    import yaml

    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")) or {}
    raw.pop("source", None)
    return AppConfig.model_validate(raw)


def _fmt(value, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def _cmd_replay(args: argparse.Namespace) -> int:
    cache_path, config_path = Path(args.cache), Path(args.config)
    cfg = _load_config_lenient(config_path)
    cache = load_cache(cache_path)
    tracks = replay(cache, cfg, confidence=args.confidence, fps_budget=args.fps_budget)
    summary = summarize(tracks)
    out = (
        Path(args.out)
        if args.out
        else Path("eval/runs") / f"{cache_path.stem}__{config_path.stem}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cache": str(cache_path),
        "config": str(config_path),
        "confidence": args.confidence if args.confidence is not None else cfg.detector.confidence,
        "fps_budget": args.fps_budget,
        "summary": summary,
        "tracks": [dataclasses.asdict(t) for t in tracks],
    }
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(
        f"{'cache':<28} {'trucks':>6} {'median_s':>9} {'<1s%':>6} {'<2s%':>6} {'max_s':>7} {'score':>6}"
    )
    print(
        f"{cache_path.stem:<28} {summary['trucks']:>6} {_fmt(summary['median_s'], '.2f'):>9} "
        f"{_fmt(summary['under_1s_pct'], '.0f'):>6} {_fmt(summary['under_2s_pct'], '.0f'):>6} "
        f"{_fmt(summary['max_s'], '.2f'):>7} {_fmt(summary['best_score_median'], '.2f'):>6}"
    )
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine.evaluation")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("cache", help="run the detector over a video and save raw detections")
    c.add_argument("--video", required=True)
    c.add_argument("--size", type=int, default=512)
    c.add_argument("--model", default="small", choices=["nano", "small", "medium"])
    c.add_argument("--floor", type=float, default=0.05)
    c.add_argument("--classes", default="car,truck,bus")
    c.add_argument("--device", default="cuda")
    c.add_argument("--out", default=None)
    c.set_defaults(func=_cmd_cache)
    r = sub.add_parser("replay", help="track cached detections with the engine's tracker")
    r.add_argument("--cache", required=True)
    r.add_argument("--config", required=True)
    r.add_argument("--confidence", type=float, default=None)
    r.add_argument("--fps-budget", type=float, default=None)
    r.add_argument("--out", default=None)
    r.set_defaults(func=_cmd_replay)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
