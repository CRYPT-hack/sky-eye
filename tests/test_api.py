from __future__ import annotations

import contextlib
import signal
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from engine.api.app import EngineContext, create_app
from engine.config import ApiConfig, AuthConfig
from engine.events.bus import EventBus
from engine.pipeline import PipelineStatus, RuntimeSettings
from engine.storage import TruckRecord, TruckRepository, media_dir

JPEG = b"\xff\xd8\xff\xe0fakejpeg\xff\xd9"
T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


class FakePipeline:
    def status(self) -> PipelineStatus:
        return PipelineStatus("running", 24.5, 100, 1, 2, T0)

    def latest_jpeg(self) -> bytes | None:
        return JPEG


@dataclass
class Rig:
    ctx: EngineContext
    repo: TruckRepository
    bus: EventBus


def make_rig(data_dir: Path, mode: str = "none", token: str | None = None) -> Rig:
    data_dir.mkdir(parents=True, exist_ok=True)
    repo = TruckRepository(data_dir / "sky-eye.db")
    bus = EventBus()
    ctx = EngineContext(
        pipeline=FakePipeline(),  # type: ignore[arg-type]
        repo=repo,
        bus=bus,
        runtime=RuntimeSettings(),
        data_dir=data_dir,
        api=ApiConfig(auth=AuthConfig(mode=mode), stream_fps=50),
        token=token,
    )
    return Rig(ctx, repo, bus)


def add_truck(repo: TruckRepository, run_id: int, n: int, snapshot: str | None = None) -> int:
    return repo.insert_truck(
        TruckRecord(
            id=None,
            run_id=run_id,
            track_id=n,
            first_seen=T0.replace(minute=n),
            last_seen=T0.replace(minute=n),
            best_score=0.9,
            snapshot_path=snapshot,
        )
    )


@pytest.fixture
def rig(tmp_path):
    r = make_rig(tmp_path / "data")
    yield r
    r.repo.close()


@pytest.fixture
def client(rig):
    with TestClient(create_app(rig.ctx)) as c:
        yield c


def test_root(client):
    assert client.get("/").json() == {"api": 1, "name": "sky-eye", "version": "0.1.0"}


def test_status_shape(client):
    r = client.get("/api/status")
    body = r.json()
    assert r.status_code == 200
    assert body["api"] == 1
    assert "fps" in body["status"]
    assert body["status"]["started_at"] == "2026-11-05T12:00:00Z"
    assert body["status"]["source_state"] == "running"


def test_trucks_list_and_detail(client, rig):
    run = rig.repo.create_run("t", "{}")
    a, b = add_truck(rig.repo, run, 1), add_truck(rig.repo, run, 2)
    body = client.get("/api/trucks").json()
    assert body["api"] == 1 and body["limit"] == 50 and body["offset"] == 0
    assert [t["id"] for t in body["trucks"]] == [b, a]
    detail = client.get(f"/api/trucks/{a}")
    assert detail.status_code == 200
    assert detail.json()["truck"]["id"] == a and detail.json()["api"] == 1
    assert client.get("/api/trucks/999").status_code == 404


def test_trucks_time_filter(client, rig):
    run = rig.repo.create_run("t", "{}")
    add_truck(rig.repo, run, 1)
    b = add_truck(rig.repo, run, 5)
    body = client.get("/api/trucks", params={"from": "2026-11-05T12:03:00"}).json()
    assert [t["id"] for t in body["trucks"]] == [b]


def test_limit_bounds(client):
    assert client.get("/api/trucks?limit=0").status_code == 422
    assert client.get("/api/trucks?limit=501").status_code == 422
    assert client.get("/api/trucks?limit=500").status_code == 200


def test_media_url_served_from_dir_with_spaces(tmp_path):
    rig = make_rig(tmp_path / "auto detection" / "data")
    try:
        day = media_dir(rig.ctx.data_dir) / "2026-11-05"
        day.mkdir()
        (day / "1_1 full.jpg").write_bytes(JPEG)
        run = rig.repo.create_run("t", "{}")
        tid = add_truck(rig.repo, run, 1, snapshot="2026-11-05/1_1 full.jpg")
        with TestClient(create_app(rig.ctx)) as c:
            url = c.get(f"/api/trucks/{tid}").json()["truck"]["snapshot_url"]
            r = c.get(url)
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content == JPEG
    finally:
        rig.repo.close()


def test_media_path_traversal_blocked(client):
    assert client.get("/media/../sky-eye.db").status_code == 404
    assert client.get("/media/%2e%2e/sky-eye.db").status_code == 404


def test_runtime_put_and_get(client, rig):
    assert client.get("/api/config/runtime").json() == {
        "api": 1,
        "runtime": {"confidence": 0.4, "sahi_enabled": False},
    }
    r = client.put("/api/config/runtime", json={"confidence": 0.6})
    assert r.status_code == 200
    assert client.get("/api/config/runtime").json()["runtime"]["confidence"] == 0.6
    assert rig.ctx.runtime.confidence == 0.6  # shared instance mutated in place
    assert client.put("/api/config/runtime", json={"confidence": 2}).status_code == 422
    assert client.put("/api/config/runtime", json={"bogus": 1}).status_code == 422
    assert client.put("/api/config/runtime", json=[1]).status_code == 422
    assert rig.ctx.runtime.confidence == 0.6


def test_ws_receives_truck_new(client, rig):
    msg = {"api": 1, "type": "truck.new", "truck": {"id": 7}}
    with client.websocket_connect("/ws") as ws:
        rig.bus.publish(msg)
        for _ in range(10):
            got = ws.receive_json()
            if got["type"] == "truck.new":
                break
        assert got == msg


def test_ws_sends_status_tick(client):
    with client.websocket_connect("/ws") as ws:
        got = ws.receive_json()
    assert got["type"] == "status" and got["api"] == 1
    assert "fps" in got["status"]


def test_ws_disconnect_does_not_break_bus(client, rig):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        assert len(rig.bus._subscribers) == 1
    for i in range(200):
        rig.bus.publish({"api": 1, "type": "truck.new", "i": i})
    deadline = time.time() + 5
    while rig.bus._subscribers and time.time() < deadline:
        time.sleep(0.05)
    assert rig.bus._subscribers == []


@contextlib.contextmanager
def live_server(app):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


def test_mjpeg_first_part(rig):
    with live_server(create_app(rig.ctx)) as base:
        with httpx.Client(timeout=5) as c, c.stream("GET", base + "/stream.mjpg") as r:
            assert r.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
            chunk = next(r.iter_bytes())
            while b"fakejpeg" not in chunk:
                chunk += next(r.iter_bytes())
    assert chunk.startswith(b"--frame")
    assert b"Content-Type: image/jpeg" in chunk
    assert JPEG in chunk


def test_token_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYEYE_API_TOKEN", "s3cret")
    rig = make_rig(tmp_path / "data", mode="token", token="s3cret")
    try:
        with TestClient(create_app(rig.ctx)) as c:
            assert c.get("/").status_code == 200
            assert c.get("/api/status").status_code == 401
            assert c.get("/api/status", headers={"X-SkyEye-Token": "nope"}).status_code == 401
            assert c.get("/api/status", headers={"X-SkyEye-Token": "s3cret"}).status_code == 200
            assert c.get("/api/status?token=s3cret").status_code == 200
            assert c.get("/media/x.jpg").status_code == 401
            with pytest.raises(WebSocketDisconnect) as exc:
                with c.websocket_connect("/ws"):
                    pass
            assert exc.value.code == 4401
            with c.websocket_connect("/ws?token=s3cret") as ws:
                assert ws.receive_json()["type"] == "status"
    finally:
        rig.repo.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_open_mjpeg_viewer_does_not_block_shutdown(tmp_path):
    from engine.app import build_server

    rig = make_rig(tmp_path / "data")
    rig.ctx.api = ApiConfig(host="127.0.0.1", port=_free_port(), stream_fps=50)
    server = build_server(rig.ctx)
    assert server.config.timeout_graceful_shutdown == 3  # backstop
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.02)
        assert server.started
        streaming = threading.Event()

        def viewer() -> None:
            url = f"http://127.0.0.1:{rig.ctx.api.port}/stream.mjpg"
            try:
                with httpx.Client(timeout=10) as c, c.stream("GET", url) as r:
                    for _ in r.iter_bytes():
                        streaming.set()
            except httpx.HTTPError:
                pass

        viewer_thread = threading.Thread(target=viewer, daemon=True)
        viewer_thread.start()
        assert streaming.wait(5)
        t0 = time.monotonic()
        server.handle_exit(signal.SIGINT, None)  # what Ctrl+C does
        thread.join(5)
        assert not thread.is_alive(), "server still waiting for the MJPEG stream"
        assert time.monotonic() - t0 < 2.5  # ended by the shutdown flag, not the backstop
        assert rig.ctx.shutdown.is_set()
        viewer_thread.join(5)
        assert not viewer_thread.is_alive()
    finally:
        server.should_exit = server.force_exit = True
        thread.join(5)
        rig.repo.close()


def test_ws_closes_on_shutdown(client, rig):
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status"
        rig.ctx.shutdown.set()
        with pytest.raises(WebSocketDisconnect) as exc:
            for _ in range(200):
                ws.receive_json()
    assert exc.value.code == 1001


def test_error_bodies_carry_api_version(client):
    r = client.get("/api/trucks/999")
    assert r.status_code == 404
    assert r.json() == {"api": 1, "error": "truck not found"}
    r = client.get("/no/such/route")
    assert r.status_code == 404 and r.json()["api"] == 1
    r = client.get("/api/trucks?limit=0")
    assert r.status_code == 422
    body = r.json()
    assert body["api"] == 1 and body["error"] == "invalid request"
    assert body["detail"][0]["loc"][-1] == "limit"
    r = client.get("/media/2026-01-01/missing.jpg")
    assert r.status_code == 404 and r.json()["api"] == 1
