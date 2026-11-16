from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

from engine.evaluation.cache import build_cache, save_cache


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
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
