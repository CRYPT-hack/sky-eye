"""End-to-end wiring check: the built website served by a live engine.

Needs `cd dashboard && npm ci && npm run build` first; deselected by default.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
from websockets.sync.client import connect

import engine.app as app_mod
from engine.app import build_engine, build_server, shutdown_engine
from engine.config import load_config
from engine.core import Detection
from engine.detectors.fake import FakeDetector
from tests.conftest import make_video

pytestmark = pytest.mark.dashboard

DIST = Path(__file__).resolve().parents[1] / "dashboard" / "dist"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RectangleFollower(FakeDetector):
    """A 'truck' box on the white rectangle that make_video draws, whatever
    frames the realtime source drops."""

    def __init__(self) -> None:
        super().__init__(lambda i: [])

    def detect(self, image: np.ndarray) -> list[Detection]:
        return _follow_rectangle(image)


def _follow_rectangle(image: np.ndarray) -> list[Detection]:
    ys, xs = np.nonzero(image[:, :, 0] > 200)
    if xs.size == 0:
        return []
    box = (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))
    return [Detection(box=box, score=0.9, class_name="truck")]


def test_built_site_served_by_live_engine(tmp_path, monkeypatch):
    if not (DIST / "index.html").is_file():
        pytest.skip("dashboard/dist is not built (cd dashboard && npm ci && npm run build)")

    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: RectangleFollower())
    video = make_video(tmp_path / "v.mp4", 180)  # 6 s at 30 fps
    port = _free_port()
    config = tmp_path / "config.yaml"
    config.write_text(
        f"source:\n  type: file\n  path: {video.as_posix()}\n  realtime: true\n"
        "detector:\n  device: cpu\n"
        f"evidence:\n  data_dir: {(tmp_path / 'data').as_posix()}\n"
        f"api:\n  host: 127.0.0.1\n  port: {port}\n  dashboard_dir: {DIST.as_posix()}\n",
        encoding="utf-8",
    )
    ctx = build_engine(load_config(config))
    server = build_server(ctx)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started
        base = f"http://127.0.0.1:{port}"

        # subscribe before the pipeline starts, so truck.new cannot be missed
        with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5, close_timeout=5) as ws,                 httpx.Client(base_url=base, timeout=5) as c:
            ctx.pipeline.start()
            r = c.get("/")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/html")
            html = r.text
            assert '<div id="root">' in html

            # the built site makes no external requests (SVG namespace URIs excepted)
            external = [
                u for u in re.findall(r"https?://[^\s\"'<>)]+", html)
                if u != "http://www.w3.org/2000/svg"
            ]
            assert external == []

            assets = re.findall(r"""(?:src|href)=["'](/assets/[^"']+)["']""", html)
            assert assets, "index.html references no /assets/ files"
            for url in assets:
                resp = c.get(url)
                assert resp.status_code == 200, url
                assert len(resp.content) > 0

            info = c.get("/api/info")
            assert info.status_code == 200
            assert info.json()["api"] == 1

            seen_status = False
            new_truck = None
            end = time.time() + 15
            while time.time() < end and new_truck is None:
                msg = json.loads(ws.recv(timeout=5))
                assert msg["api"] == 1
                if msg["type"] == "status":
                    seen_status = True
                elif msg["type"] == "truck.new":
                    new_truck = msg
            assert seen_status
            assert new_truck is not None, "no truck.new within 15 s"

            crop_url = new_truck["truck"]["crop_url"]
            assert "?v=" in crop_url
            crop = c.get(crop_url)
            assert crop.status_code == 200
            assert crop.headers["content-type"] == "image/jpeg"
    finally:
        t0 = time.time()
        server.should_exit = True
        thread.join(timeout=5)
        shutdown_engine(ctx)
        assert not thread.is_alive()
        assert time.time() - t0 < 5
