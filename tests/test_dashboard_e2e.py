"""End-to-end wiring check: the built website served by a live engine.

Needs `cd dashboard && npm ci && npm run build` first; deselected by default.
"""
from __future__ import annotations

import json
import math
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
            app_mod.on_started(ctx, f"{base}/")  # what main() runs once listening
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
        assert not ctx.alerts._alive()


class BoxRecordingFollower(RectangleFollower):
    """RectangleFollower that keeps every box it reports."""

    def __init__(self) -> None:
        super().__init__()
        self.boxes: list[tuple[float, float, float, float]] = []

    def detect(self, image: np.ndarray) -> list[Detection]:
        dets = super().detect(image)
        self.boxes.extend(d.box for d in dets)
        return dets


def _expected_ground_point(box, frame_size, lat0, lon0, alt_m, hfov_deg):
    """Hand calculation for a camera looking straight down with image-up = north:
    the bottom-centre pixel's offset from the frame centre, scaled by height/focal
    length, is the east/north distance in metres from the drone."""
    x1, _, x2, y2 = box
    w, h = frame_size
    f = (w / 2) / math.tan(math.radians(hfov_deg) / 2)  # focal length in pixels
    east = alt_m * ((x1 + x2) / 2 - w / 2) / f
    north = -alt_m * (y2 - h / 2) / f  # lower in the image = further south
    a, e2 = 6378137.0, 0.00669437999014
    phi = math.radians(lat0)
    s2 = 1 - e2 * math.sin(phi) ** 2
    r_n = a / math.sqrt(s2)
    r_m = a * (1 - e2) / s2 ** 1.5
    return (
        lat0 + math.degrees(north / r_m),
        lon0 + math.degrees(east / (r_n * math.cos(phi))),
    )


def _metres_between(lat1, lon1, lat2, lon2):
    return math.hypot(
        math.radians(lat2 - lat1) * 6371000.0,
        math.radians(lon2 - lon1) * 6371000.0 * math.cos(math.radians(lat1)),
    )


def _srt_block(n: int, t0: float, t1: float, lat: float, lon: float) -> str:
    def stamp(t: float) -> str:
        ms = round(t * 1000)
        return f"00:00:{ms // 1000:02d},{ms % 1000:03d}"

    return (
        f"{n}\n{stamp(t0)} --> {stamp(t1)}\n<font size=\"28\">SrtCnt : {n}\n"
        f"[latitude: {lat:.6f}] [longitude: {lon:.6f}] "
        "[rel_alt: 100.000 abs_alt: 600.000] [gb_yaw: 0.0 gb_pitch: -90.0 gb_roll: 0.0]"
        "</font>\n\n"
    )


def test_truck_geolocated_from_srt_and_map_page_served(tmp_path, monkeypatch):
    if not (DIST / "index.html").is_file():
        pytest.skip("dashboard/dist is not built (cd dashboard && npm ci && npm run build)")

    lat0, lon0, alt, hfov = 48.137154, 11.576124, 100.0, 90.0
    detector = BoxRecordingFollower()
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: detector)
    monkeypatch.setattr(app_mod, "_build_notifiers", lambda cfg, data_dir: [RecordingNotifier()])
    video = make_video(tmp_path / "flight.mp4", 120)  # 4 s at 30 fps, 320x240
    # a pose every 0.25 s (the matcher accepts a pose within 0.5 s of the frame)
    (tmp_path / "flight.SRT").write_text(
        "".join(_srt_block(i + 1, i * 0.25, (i + 1) * 0.25, lat0, lon0) for i in range(16)),
        encoding="utf-8",
    )
    port = _free_port()
    config = tmp_path / "config.yaml"
    config.write_text(
        f"source:\n  type: file\n  path: {video.as_posix()}\n  realtime: true\n"
        "detector:\n  device: cpu\n"
        "telemetry:\n  type: dji_srt\n"
        f"camera:\n  hfov_deg: {hfov}\n"
        f"evidence:\n  data_dir: {(tmp_path / 'data').as_posix()}\n"
        f"api:\n  host: 127.0.0.1\n  port: {port}\n  dashboard_dir: {DIST.as_posix()}\n",
        encoding="utf-8",
    )
    ctx = build_engine(load_config(config))
    assert ctx.telemetry.state == "ok"  # parsed at build time, before any frame
    server = build_server(ctx)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started
        base = f"http://127.0.0.1:{port}"
        with connect(f"ws://127.0.0.1:{port}/ws", open_timeout=5, close_timeout=5) as ws, \
                httpx.Client(base_url=base, timeout=5) as c:
            app_mod.on_started(ctx, f"{base}/")
            new_truck = None
            ended = False
            end = time.time() + 30
            while time.time() < end and not ended:
                try:
                    msg = json.loads(ws.recv(timeout=2))
                except TimeoutError:
                    continue
                if msg["type"] == "truck.new" and new_truck is None:
                    new_truck = msg["truck"]
                elif msg["type"] == "status" and msg["status"]["source_state"] == "ended":
                    ended = True
            assert new_truck is not None and ended

            truck = c.get(f"/api/trucks/{new_truck['id']}").json()["truck"]
            assert truck["geo_status"] == "ok"
            assert truck["geo_error_m"] is not None and truck["geo_error_m"] < 200
            assert detector.boxes
            # The rectangle moves 2 px per frame (about 1.25 m on the ground), so the
            # stored point must be the hand-computed one of one of the boxes seen.
            expected = [
                _expected_ground_point(b, (320, 240), lat0, lon0, alt, hfov)
                for b in detector.boxes
            ]
            nearest = min(_metres_between(truck["lat"], truck["lon"], *p) for p in expected)
            assert nearest < 0.5, f"stored point is {nearest:.2f} m from every expected point"
            # sanity on direction: the box is left of centre and below it -> west/south
            assert truck["lon"] < lon0 and truck["lat"] < lat0

            tel = c.get("/api/telemetry")
            assert tel.status_code == 200
            body = tel.json()
            assert body["state"] == "ok"
            assert body["track"], "no drone track"
            assert body["track"][0] == pytest.approx([lat0, lon0], abs=1e-6)
            assert body["latest"]["alt_agl_m"] == pytest.approx(alt)

            info = c.get("/api/info").json()["info"]
            assert info["telemetry"] == "dji_srt"
            assert info["map_tile_url"].startswith("https://")

            # the Map page is a lazy chunk: the main script names it, and it is served
            html = c.get("/").text
            main = re.search(r"""src=["'](/assets/index-[^"']+\.js)["']""", html)
            assert main, "index.html references no main script"
            main_js = c.get(main.group(1))
            assert main_js.status_code == 200
            chunk = re.search(r"MapPage-[A-Za-z0-9_-]+\.js", main_js.text)
            assert chunk, "main script does not reference the MapPage chunk"
            resp = c.get(f"/assets/{chunk.group(0)}")
            assert resp.status_code == 200
            assert "leaflet" in resp.text.lower()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        shutdown_engine(ctx)
        assert not thread.is_alive()
