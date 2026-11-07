from __future__ import annotations

import asyncio
import contextlib
import json
import logging
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
from engine.telemetry import DronePose
from tests.conftest import FakeTelemetry

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


INFO = {
    "version": "0.1.0",
    "source": {"type": "file", "name": "clip.mp4", "fps": 25.0},
    "detector": {
        "model": "rfdetr-small", "input_size": 512, "device": "cpu", "gpu": None,
        "classes": ["truck"],
    },
    "auth": "none",
}


def make_rig(
    data_dir: Path,
    mode: str = "none",
    token: str | None = None,
    dashboard_dir: Path | None = None,
    telemetry=None,
    telemetry_type: str = "none",
) -> Rig:
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
        info={**INFO, "auth": mode},
        dashboard_dir=dashboard_dir,
        **({"telemetry": telemetry, "telemetry_type": telemetry_type} if telemetry else {}),
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


def test_api_root_json(client):
    assert client.get("/api").json() == {"api": 1, "name": "sky-eye", "version": "0.1.0"}


def make_dist(root: Path) -> Path:
    dist = root / "dist dir"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<html><div id="root"></div></html>', encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return dist


def test_root_serves_dashboard_index(tmp_path):
    rig = make_rig(tmp_path / "data", dashboard_dir=make_dist(tmp_path))
    try:
        with TestClient(create_app(rig.ctx)) as c:
            r = c.get("/")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/html")
            assert r.headers["cache-control"] == "no-cache"
            assert '<div id="root">' in r.text
            assert c.get("/assets/app.js").status_code == 200
            assert c.get("/api/info").json()["info"]["dashboard"] is True
    finally:
        rig.repo.close()


def test_root_without_dashboard_explains_build(tmp_path):
    rig = make_rig(tmp_path / "data", dashboard_dir=tmp_path / "nope")
    try:
        with TestClient(create_app(rig.ctx)) as c:
            r = c.get("/")
            assert r.status_code == 200
            body = r.json()
            assert body["api"] == 1 and body["name"] == "sky-eye"
            assert "npm run build" in body["dashboard"]
            assert c.get("/assets/app.js").status_code == 404
            assert c.get("/api/info").json()["info"]["dashboard"] is False
    finally:
        rig.repo.close()


HINT = "not built - run: cd dashboard && npm ci && npm run build, then reload this page"


def test_dashboard_built_while_running_is_served_without_restart(tmp_path, caplog):
    rig = make_rig(tmp_path / "data", dashboard_dir=tmp_path / "dist dir")
    try:
        with caplog.at_level(logging.WARNING, logger="engine.api.app"):
            app = create_app(rig.ctx)
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("index.html" in m and "npm run build" in m for m in warnings)
        with TestClient(app) as c:
            r = c.get("/")
            assert r.status_code == 200 and r.json()["dashboard"] == HINT
            missing = c.get("/assets/app.js")
            assert missing.status_code == 404 and missing.json()["api"] == 1
            assert c.get("/api/info").json()["info"]["dashboard"] is False

            make_dist(tmp_path)  # `npm run build` finishes while the engine runs

            r = c.get("/")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/html")
            assert '<div id="root">' in r.text
            assert c.get("/assets/app.js").status_code == 200
            assert c.get("/api/info").json()["info"]["dashboard"] is True
    finally:
        rig.repo.close()


def test_info_shape(client):
    body = client.get("/api/info").json()
    assert body["api"] == 1
    info = body["info"]
    assert set(info) == {"version", "source", "detector", "auth", "dashboard"}
    assert "/" not in info["source"]["name"] and "\\" not in info["source"]["name"]
    assert info["dashboard"] is False


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
        "runtime": {"confidence": 0.4, "sahi_enabled": False, "alerts": {}},
    }
    r = client.put("/api/config/runtime", json={"confidence": 0.6})
    assert r.status_code == 200
    assert client.get("/api/config/runtime").json()["runtime"]["confidence"] == 0.6
    assert rig.ctx.runtime.confidence == 0.6  # shared instance mutated in place
    assert client.put("/api/config/runtime", json={"confidence": 2}).status_code == 422
    assert client.put("/api/config/runtime", json={"bogus": 1}).status_code == 422
    assert client.put("/api/config/runtime", json=[1]).status_code == 422
    assert rig.ctx.runtime.confidence == 0.6


def test_runtime_put_requires_json_content_type(client, rig):
    # A cross-site form or text/plain fetch cannot send application/json without
    # a CORS preflight, which the engine never answers.
    body = b'{"confidence": 0.6}'
    for headers in ({"Content-Type": "text/plain"}, {}):
        r = client.put("/api/config/runtime", content=body, headers=headers)
        assert r.status_code == 415
        assert r.json() == {"api": 1, "error": "expected application/json"}
    assert rig.ctx.runtime.confidence == 0.4
    r = client.put(
        "/api/config/runtime",
        content=body,
        headers={"Content-Type": "Application/JSON; charset=utf-8"},
    )
    assert r.status_code == 200
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
            assert c.get("/api").status_code == 401
            assert c.get("/api", headers={"X-SkyEye-Token": "s3cret"}).status_code == 200
            assert c.get("/api/status").status_code == 401
            assert c.get("/api/status", headers={"X-SkyEye-Token": "nope"}).status_code == 401
            assert c.get("/api/status", headers={"X-SkyEye-Token": "s3cret"}).status_code == 200
            assert c.get("/api/status?token=s3cret").status_code == 200
            assert c.get("/media/x.jpg").status_code == 401
            with pytest.raises(WebSocketDisconnect) as exc:
                with c.websocket_connect("/ws") as ws:  # accepted, then closed with 4401
                    ws.receive_json()
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


def test_token_mode_dashboard_shell_public(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYEYE_API_TOKEN", "s3cret")
    rig = make_rig(
        tmp_path / "data", mode="token", token="s3cret", dashboard_dir=make_dist(tmp_path)
    )
    try:
        with TestClient(create_app(rig.ctx)) as c:
            assert c.get("/").status_code == 200
            assert c.get("/assets/app.js").status_code == 200
            assert c.get("/api/info").status_code == 401
            r = c.get("/api/info", headers={"X-SkyEye-Token": "s3cret"})
            assert r.status_code == 200 and r.json()["info"]["auth"] == "token"
    finally:
        rig.repo.close()


def _raw_request(app, method: str, path: str, raw_path: bytes | None = None) -> tuple[int, bytes]:
    """Call the ASGI app with an exact path: HTTP clients normalise '..' away, a raw
    socket does not, and uvicorn hands the percent-decoded path to the app."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": raw_path if raw_path is not None else path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    messages: list[dict] = []
    requested = False

    async def receive() -> dict:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # the client stays connected until the response ends
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        messages.append(message)

    asyncio.run(asyncio.wait_for(app(scope, receive, send), timeout=10))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, body


def test_token_mode_public_shell_does_not_leak_through_odd_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYEYE_API_TOKEN", "s3cret")
    rig = make_rig(
        tmp_path / "data", mode="token", token="s3cret", dashboard_dir=make_dist(tmp_path)
    )
    (media_dir(rig.ctx.data_dir) / "x.jpg").write_bytes(JPEG)
    try:
        app = create_app(rig.ctx)
        # the helper itself works: the public shell is served, the data needs the token
        assert _raw_request(app, "GET", "/assets/app.js") == (200, b"console.log(1)")
        assert _raw_request(app, "GET", "/api/info")[0] == 401
        cases = [
            ("GET", "/assets/../api/info", None, 404),
            ("GET", "/assets/../api/info", b"/assets/%2e%2e/api/info", 404),
            ("GET", "/assets/%2e%2e/api/info", None, 404),
            ("GET", "/assets/../../data/sky-eye.db", None, 404),
            ("GET", "/assets/..\\..\\data\\sky-eye.db", None, 404),
            ("GET", "/assets/../../data/media/x.jpg", None, 404),
            ("GET", "/assetsX", None, 401),
            ("GET", "/assets", None, 401),
            ("POST", "/", None, 401),
            ("HEAD", "/", None, 401),
            ("PUT", "/assets/app.js", None, 401),
        ]
        for method, path, raw, expected in cases:
            status, body = _raw_request(app, method, path, raw)
            assert status == expected, (method, path, raw, status)
            assert b'"info"' not in body and b"SQLite format" not in body
            assert b"fakejpeg" not in body
    finally:
        rig.repo.close()


def test_ws_unauthorized_close_code_seen_by_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYEYE_API_TOKEN", "s3cret")
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    rig = make_rig(tmp_path / "data", mode="token", token="s3cret")
    try:
        with live_server(create_app(rig.ctx)) as base:
            url = base.replace("http", "ws", 1) + "/ws"
            with connect(url, open_timeout=5, close_timeout=5) as ws:  # accepted, then closed
                with pytest.raises(ConnectionClosed):
                    ws.recv(timeout=5)
                assert ws.protocol.close_code == 4401
            with connect(url + "?token=s3cret", open_timeout=5, close_timeout=5) as ws:
                assert json.loads(ws.recv(timeout=5))["type"] == "status"
    finally:
        rig.repo.close()


class FakeAlerts:
    def __init__(self):
        self.tests = 0

    def send_test(self):
        self.tests += 1


def test_runtime_alert_toggles(rig):
    rig.ctx.runtime.alerts = {"desktop": True, "telegram": True}
    with TestClient(create_app(rig.ctx)) as client:
        assert client.get("/api/config/runtime").json()["runtime"]["alerts"] == {
            "desktop": True, "telegram": True}
        r = client.put("/api/config/runtime", json={"alerts": {"desktop": False}})
        assert r.status_code == 200
        assert r.json()["runtime"]["alerts"] == {"desktop": False, "telegram": True}
        assert rig.ctx.runtime.alerts == {"desktop": False, "telegram": True}
        r = client.put("/api/config/runtime", json={"alerts": {"sms": True}})
        assert r.status_code == 422
        assert r.json() == {"api": 1, "error": "unknown alert channel: sms"}
        assert rig.ctx.runtime.alerts == {"desktop": False, "telegram": True}
        assert client.put("/api/config/runtime", json={"alerts": [1]}).status_code == 422
        assert client.put(
            "/api/config/runtime", json={"alerts": {"desktop": "x"}}).status_code == 422
        # an unrelated PUT keeps the toggles
        client.put("/api/config/runtime", json={"confidence": 0.5})
        assert rig.ctx.runtime.alerts == {"desktop": False, "telegram": True}


def test_alert_test_endpoint(rig):
    fake = FakeAlerts()
    rig.ctx.alerts = fake  # type: ignore[assignment]
    rig.ctx.runtime.alerts = {"desktop": True, "telegram": False, "mqtt": True}
    with TestClient(create_app(rig.ctx)) as client:
        r = client.post("/api/alerts/test", json={})
        assert r.status_code == 200
        assert r.json() == {"api": 1, "sent": ["desktop", "mqtt"]}
        assert fake.tests == 1
        r = client.post("/api/alerts/test", content="{}", headers={"Content-Type": "text/plain"})
        assert r.status_code == 415 and r.json()["api"] == 1
        assert fake.tests == 1


def test_alert_test_endpoint_no_channels(rig):
    with TestClient(create_app(rig.ctx)) as client:  # no dispatcher
        r = client.post("/api/alerts/test", json={})
        assert r.status_code == 409
        assert r.json() == {"api": 1, "error": "no alert channels configured"}
    rig.ctx.alerts = FakeAlerts()  # type: ignore[assignment]
    with TestClient(create_app(rig.ctx)) as client:  # dispatcher but no channels
        assert client.post("/api/alerts/test", json={}).status_code == 409


def test_alert_test_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYEYE_API_TOKEN", "s3cret")
    r = make_rig(tmp_path / "d2", mode="token", token="s3cret")
    r.ctx.alerts = FakeAlerts()  # type: ignore[assignment]
    r.ctx.runtime.alerts = {"desktop": True}
    try:
        with TestClient(create_app(r.ctx)) as client:
            assert client.post("/api/alerts/test", json={}).status_code == 401
            ok = client.post(
                "/api/alerts/test", json={}, headers={"X-SkyEye-Token": "s3cret"}
            )
            assert ok.status_code == 200
    finally:
        r.repo.close()


# --- telemetry ---
def _poses(n: int) -> list[DronePose]:
    return [DronePose(1.7e9 + i, 48.0 + i * 1e-5, 11.0, 50.0, 90.0, -60.0) for i in range(n)]


def _tel_client(tmp_path, telemetry, telemetry_type="mavlink"):
    rig = make_rig(tmp_path / "data", telemetry=telemetry, telemetry_type=telemetry_type)
    return rig, TestClient(create_app(rig.ctx))


def test_telemetry_endpoint_and_ws(tmp_path):
    poses = _poses(3)
    tel = FakeTelemetry(poses[-1], track=poses)
    rig, client = _tel_client(tmp_path, tel)
    try:
        with client:
            body = client.get("/api/telemetry?seconds=60").json()
            assert body["api"] == 1 and body["state"] == "ok"
            assert body["latest"] == {
                "lat": poses[-1].lat, "lon": 11.0, "alt_agl_m": 50.0, "yaw_deg": 90.0,
                "t_iso": datetime.fromtimestamp(poses[-1].ts, tz=timezone.utc)
                .isoformat().replace("+00:00", "Z"),
            }
            assert body["track"] == [[p.lat, p.lon] for p in poses]
            with client.websocket_connect("/ws") as ws:
                seen = []
                for _ in range(10):
                    msg = ws.receive_json()
                    seen.append(msg["type"])
                    if msg["type"] == "telemetry":
                        assert msg["api"] == 1 and msg["latest"]["lat"] == poses[-1].lat
                        break
                assert "telemetry" in seen
    finally:
        rig.repo.close()


def test_telemetry_endpoint_without_telemetry(client):
    body = client.get("/api/telemetry").json()
    assert body == {"api": 1, "state": "none", "latest": None, "track": []}


def test_telemetry_ws_silent_without_telemetry(client):
    with client.websocket_connect("/ws") as ws:
        types = [ws.receive_json()["type"] for _ in range(2)]  # two status ticks, 1 s apart
    assert types == ["status", "status"]


def test_telemetry_t_iso_null_for_srt(tmp_path):
    tel = FakeTelemetry(_poses(1)[0], track=_poses(1))
    rig, client = _tel_client(tmp_path, tel, "dji_srt")
    try:
        with client:
            assert client.get("/api/telemetry").json()["latest"]["t_iso"] is None
    finally:
        rig.repo.close()


@pytest.mark.parametrize("seconds", [0, 4000, -1])
def test_telemetry_seconds_bounds(client, seconds):
    r = client.get(f"/api/telemetry?seconds={seconds}")
    assert r.status_code == 422 and r.json()["api"] == 1


def test_telemetry_track_thinned(tmp_path):
    poses = _poses(2500)
    tel = FakeTelemetry(poses[-1], track=poses)
    rig, client = _tel_client(tmp_path, tel)
    try:
        with client:
            track = client.get("/api/telemetry?seconds=3600").json()["track"]
        assert len(track) <= 600
        assert track[-1] == [poses[-1].lat, poses[-1].lon]
        assert track[0] == [poses[0].lat, poses[0].lon]
        assert track == sorted(track)  # still in time order
    finally:
        rig.repo.close()


def test_status_has_telemetry_state(tmp_path):
    rig = make_rig(tmp_path / "data")
    try:
        class P(FakePipeline):
            def status(self):
                return PipelineStatus("running", 1.0, 1, 0, 0, T0, telemetry_state="stale")

        rig.ctx.pipeline = P()  # type: ignore[assignment]
        with TestClient(create_app(rig.ctx)) as c:
            assert c.get("/api/status").json()["status"]["telemetry_state"] == "stale"
    finally:
        rig.repo.close()


def test_telemetry_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("SKYEYE_API_TOKEN", "tok")
    rig = make_rig(tmp_path / "data", mode="token", token="tok")
    try:
        with TestClient(create_app(rig.ctx)) as c:
            assert c.get("/api/telemetry").status_code == 401
            ok = c.get("/api/telemetry", headers={"X-SkyEye-Token": "tok"})
            assert ok.status_code == 200
    finally:
        rig.repo.close()
