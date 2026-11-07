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


class RecordingNotifier:
    """Stands in for the real desktop notifier; keeps every batch it is given."""

    name = "desktop"

    def __init__(self) -> None:
        self.batches: list = []

    def send(self, batch) -> None:
        self.batches.append(batch)


def _wait_for(predicate, timeout: float):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_built_site_served_by_live_engine(tmp_path, monkeypatch):
    if not (DIST / "index.html").is_file():
        pytest.skip("dashboard/dist is not built (cd dashboard && npm ci && npm run build)")

    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: RectangleFollower())
    fake = RecordingNotifier()
    monkeypatch.setattr(app_mod, "_build_notifiers", lambda cfg, data_dir: [fake])
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
            ctx.alerts.start()  # what on_started does, before the pipeline
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

            # alerts: the one truck gives exactly one batch carrying its id
            truck_id = new_truck["truck"]["id"]
            assert _wait_for(lambda: len(fake.batches) >= 1, 5)
            # let the whole video play out, counting every truck.new on the bus:
            # a second one would be held by the dispatcher for the cooldown
            # and so never show up as a delivered batch within a short wait
            new_msgs = [new_truck]
            ended = False
            end = time.time() + 30
            while time.time() < end and not ended:
                try:
                    msg = json.loads(ws.recv(timeout=2))
                except TimeoutError:
                    continue
                if msg["type"] == "truck.new":
                    new_msgs.append(msg)
                elif msg["type"] == "status" and msg["status"]["source_state"] == "ended":
                    ended = True
            assert ended, "video did not end within 30 s"
            assert [m["truck"]["id"] for m in new_msgs] == [truck_id]
            real = [b for b in fake.batches if not b.is_test]
            assert len(real) == 1
            assert [t["id"] for t in real[0].trucks] == [truck_id]

            # the test button: a test batch reaches the channel
            r = c.post("/api/alerts/test", json={})
            assert r.status_code == 200
            assert r.json() == {"api": 1, "sent": ["desktop"]}
            assert _wait_for(lambda: any(b.is_test for b in fake.batches), 5)
            assert len([b for b in fake.batches if not b.is_test]) == 1
    finally:
        t0 = time.time()
        server.should_exit = True
        thread.join(timeout=5)
        shutdown_engine(ctx)
        assert not thread.is_alive()
        assert time.time() - t0 < 5
