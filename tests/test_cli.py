from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import engine.app as app_mod
from engine.alerts.core import AlertDispatcher
from engine.app import build_engine, load_dotenv, main
from engine.config import load_config
from engine.detectors.fake import FakeDetector
from engine.events.bus import EventBus
from tests.conftest import make_video


def write_config(tmp_path, extra_detector: str = "", source: str = "", extra: str = "") -> object:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"source:\n  type: file\n{source}\n"
        f"detector:\n  device: cpu\n{extra_detector}\n"
        f"evidence:\n  data_dir: data\n{extra}",
        encoding="utf-8",
    )
    return cfg


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_bad_config_exit_code_2(tmp_path, capsys):
    cfg = write_config(tmp_path, "  confidence: 1.5")
    assert main(["--config", str(cfg), "--check"]) == 2
    err = capsys.readouterr().err
    assert "detector.confidence" in err
    assert "Traceback" not in err


def test_missing_weights_exit_code_2(tmp_path, capsys):
    cfg = write_config(tmp_path, "  weights: nope.pth")
    assert main(["--config", str(cfg), "--check"]) == 2
    err = capsys.readouterr().err
    assert "nope.pth" in err
    assert "Traceback" not in err


def test_missing_config_exit_code_2(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "absent.yaml"), "--check"]) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_check_prints_ok(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    cfg = write_config(tmp_path)
    assert main(["--config", str(cfg), "--check"]) == 0
    assert capsys.readouterr().out.strip() == "OK"


def test_file_source_without_path_exit_code_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    cfg = write_config(tmp_path)
    assert main(["--config", str(cfg)]) == 2
    err = capsys.readouterr().err
    assert "source.path" in err
    assert "Traceback" not in err


def test_build_engine_with_fake_detector(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    video = make_video(tmp_path / "v.mp4", 5)
    cfg = load_config(write_config(tmp_path, source=f"  path: {video.as_posix()}"))
    stale = cfg.evidence.data_dir / "media" / "2026-11-05" / "1_3.mp4.part"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"left by a forced exit")
    ctx = build_engine(cfg)
    try:
        assert not stale.exists()  # partial clips from an earlier run are removed
        assert ctx.pipeline.status().source_state == "starting"
        assert ctx.repo.count_trucks(1) == 0  # run 1 exists
        assert ctx.data_dir == cfg.evidence.data_dir
        assert ctx.token is None
    finally:
        ctx.pipeline.stop()
        ctx.repo.close()


def test_build_engine_info(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    folder = tmp_path / "samples dir"
    folder.mkdir()
    video = make_video(folder / "clip.mp4", 5)
    cfg = load_config(write_config(tmp_path, source=f"  path: {video.as_posix()}"))
    ctx = build_engine(cfg)
    try:
        info = ctx.info
        assert info["version"] == "0.1.0" and info["auth"] == "none"
        assert info["source"]["type"] == "file" and info["source"]["name"] == "clip.mp4"
        assert info["source"]["fps"] > 0
        assert info["detector"] == {
            "model": "rfdetr-small", "input_size": 512, "device": "cpu", "gpu": None,
            "classes": ["truck"],
        }
        expected = Path(app_mod.__file__).resolve().parents[1] / "dashboard" / "dist"
        assert ctx.dashboard_dir == expected
    finally:
        ctx.pipeline.stop()
        ctx.repo.close()


def test_build_engine_dashboard_dir_override(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    video = make_video(tmp_path / "v.mp4", 5)
    cfg = load_config(
        write_config(
            tmp_path, source=f"  path: {video.as_posix()}", extra="api:\n  dashboard_dir: web\n"
        )
    )
    ctx = build_engine(cfg)
    try:
        assert ctx.dashboard_dir == tmp_path / "web"
    finally:
        ctx.pipeline.stop()
        ctx.repo.close()


def test_build_engine_closes_source_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    video = make_video(tmp_path / "v.mp4", 5)
    cfg = load_config(write_config(tmp_path, source=f"  path: {video.as_posix()}"))
    closed = []
    real = app_mod.build_source

    def spy(scfg):
        src = real(scfg)
        orig = src.close
        src.close = lambda: (closed.append(1), orig())[1]
        return src

    monkeypatch.setattr(app_mod, "build_source", spy)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_mod, "EvidenceRecorder", boom)
    with pytest.raises(RuntimeError):
        build_engine(cfg)
    assert closed


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# comment\nSKY_A=one\nSKY_B=\"two words\"\nSKY_C='three'\n\nSKY_D = four\nbad line\n",
        encoding="utf-8",
    )
    for k in ("SKY_A", "SKY_B", "SKY_C", "SKY_D"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SKY_A", "preset")
    load_dotenv(tmp_path / ".env")
    assert os.environ["SKY_A"] == "preset"
    assert os.environ["SKY_B"] == "two words"
    assert os.environ["SKY_C"] == "three"
    assert os.environ["SKY_D"] == "four"
    for k in ("SKY_B", "SKY_C", "SKY_D"):
        monkeypatch.delenv(k)


def test_load_dotenv_missing_file_is_noop(tmp_path):
    load_dotenv(tmp_path / ".env")


def test_unopenable_video_exit_code_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    bad = tmp_path / "bad.mp4"
    bad.write_text("not a video", encoding="utf-8")
    cfg = write_config(tmp_path, source=f"  path: {bad.as_posix()}")
    assert main(["--config", str(cfg)]) == 2
    err = capsys.readouterr().err
    assert "source.path" in err and "bad.mp4" in err
    assert "Traceback" not in err


def test_failed_open_still_closes_source(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    video = make_video(tmp_path / "v.mp4", 5)
    cfg = load_config(write_config(tmp_path, source=f"  path: {video.as_posix()}"))
    closed = []

    class Src:
        def open(self):
            raise RuntimeError("half open")

        def close(self):
            closed.append(1)

    monkeypatch.setattr(app_mod, "build_source", lambda scfg: Src())
    with pytest.raises(app_mod.ConfigError):
        build_engine(cfg)
    assert closed


def test_build_engine_failure_closes_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    video = make_video(tmp_path / "v.mp4", 5)
    cfg = load_config(write_config(tmp_path, source=f"  path: {video.as_posix()}"))
    repos = []
    real = app_mod.TruckRepository

    def spy(path):
        r = real(path)
        orig = r.close
        r.close = lambda: (repos.append(1), orig())[1]
        return r

    monkeypatch.setattr(app_mod, "TruckRepository", spy)

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(app_mod, "EventManager", boom)
    with pytest.raises(RuntimeError):
        build_engine(cfg)
    assert repos


def test_dotenv_bom_and_crlf(tmp_path, monkeypatch):
    monkeypatch.delenv("SKYEYE_API_TOKEN", raising=False)
    (tmp_path / ".env").write_bytes(b"\xef\xbb\xbfSKYEYE_API_TOKEN=secret\r\nOTHER=1\r\n")
    load_dotenv(tmp_path / ".env")
    assert os.environ["SKYEYE_API_TOKEN"] == "secret"
    monkeypatch.delenv("SKYEYE_API_TOKEN")
    monkeypatch.delenv("OTHER", raising=False)


def test_undecodable_dotenv_exit_code_2(tmp_path, capsys):
    cfg = write_config(tmp_path)
    (tmp_path / ".env").write_bytes(b"\xff\xfe\x00bad\x80\x81")
    assert main(["--config", str(cfg), "--check"]) == 2
    err = capsys.readouterr().err
    assert ".env" in err and "Traceback" not in err


def test_port_in_use_exit_code_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    video = make_video(tmp_path / "v.mp4", 5)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        cfg = write_config(
            tmp_path,
            source=f"  path: {video.as_posix()}",
            extra=f"api:\n  host: 127.0.0.1\n  port: {port}\n",
        )
        assert main(["--config", str(cfg)]) == 2
    out, err = capsys.readouterr()
    assert str(port) in err and "api.port" in err
    assert "Traceback" not in err
    assert len(err.strip().splitlines()) == 1  # one paragraph
    assert "Dashboard" not in out
    assert not (tmp_path / "data" / "sky-eye.db").exists()  # no run was started


def test_main_starts_pipeline_after_bind_and_stops_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    servers = []

    class Recording(app_mod.EngineServer):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            servers.append(self)

    monkeypatch.setattr(app_mod, "EngineServer", Recording)
    video = make_video(tmp_path / "v.mp4", 30)
    port = _free_port()
    cfg = write_config(
        tmp_path,
        source=f"  path: {video.as_posix()}\n  realtime: false",
        extra=f"api:\n  host: 127.0.0.1\n  port: {port}\n",
    )
    result: dict = {}
    thread = threading.Thread(
        target=lambda: result.setdefault("code", main(["--config", str(cfg)])), daemon=True
    )
    thread.start()
    try:
        deadline = time.time() + 30
        while not (servers and servers[0].started) and time.time() < deadline:
            time.sleep(0.05)
        assert servers and servers[0].started
        status = httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=5).json()["status"]
        assert status["source_state"] in ("starting", "running", "ended")
        assert "Dashboard: http://" in capsys.readouterr().out
        servers[0].handle_exit(signal.SIGINT, None)
        thread.join(10)
        assert not thread.is_alive()
        assert result["code"] == 0
    finally:
        if servers:
            servers[0].should_exit = servers[0].force_exit = True
        thread.join(5)


def test_unclean_stop_exits_1_after_logging_shutdown(monkeypatch):
    events: list = []
    release = threading.Event()
    monkeypatch.setattr(app_mod, "_STOP_TIMEOUT_S", 0.2)
    monkeypatch.setattr(app_mod.logging, "shutdown", lambda: events.append("logging.shutdown"))

    def hard_exit(code):
        events.append(("exit", code))
        raise SystemExit(code)

    monkeypatch.setattr(app_mod, "_hard_exit", hard_exit)
    repo_closed: list = []
    ctx = SimpleNamespace(
        pipeline=SimpleNamespace(stop=lambda: release.wait(10)),  # never stops in time
        repo=SimpleNamespace(close=lambda: repo_closed.append(1)),
        shutdown=threading.Event(),
    )
    try:
        with pytest.raises(SystemExit):
            app_mod.shutdown_engine(ctx)
    finally:
        release.set()
    assert events == ["logging.shutdown", ("exit", 1)]
    assert ctx.shutdown.is_set()
    assert repo_closed == []  # the pipeline may still be using it


def test_clean_stop_closes_repo():
    repo_closed: list = []
    ctx = SimpleNamespace(
        pipeline=SimpleNamespace(stop=lambda: None),
        repo=SimpleNamespace(close=lambda: repo_closed.append(1)),
        shutdown=threading.Event(),
    )
    app_mod.shutdown_engine(ctx)
    assert repo_closed == [1] and ctx.shutdown.is_set()


def _wait_until(pred, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


class _StuckCloseNotifier:
    """Delivers at once; close() hangs (e.g. a dead broker) until released."""

    name = "mqtt"

    def __init__(self, release: threading.Event) -> None:
        self.batches: list = []
        self._release = release

    def send(self, batch) -> None:
        self.batches.append(batch)

    def close(self) -> None:
        self._release.wait(30)


def _truck_new(i: int) -> dict:
    return {"api": 1, "type": "truck.new", "truck": {"id": i}}


def test_alerts_shutdown_is_bounded_and_repo_always_closes(caplog):
    caplog.set_level(logging.INFO)
    release = threading.Event()
    notifier = _StuckCloseNotifier(release)
    bus = EventBus()
    dispatcher = AlertDispatcher(bus, [notifier], enabled=lambda name: True, cooldown_s=60)
    repo_closed: list = []
    ctx = SimpleNamespace(
        pipeline=SimpleNamespace(stop=lambda: None),
        repo=SimpleNamespace(close=lambda: repo_closed.append(1)),
        shutdown=threading.Event(),
        alerts=dispatcher,
        notifiers=[notifier],
    )
    dispatcher.start()
    try:
        bus.publish(_truck_new(1))
        assert _wait_until(lambda: len(notifier.batches) == 1)
        bus.publish(_truck_new(2))  # held by the 60 s cooldown
        assert _wait_until(lambda: len(dispatcher._channels[0].pending) == 1)
        t0 = time.monotonic()
        app_mod.shutdown_engine(ctx)
        took = time.monotonic() - t0
    finally:
        release.set()
        dispatcher.stop(timeout=1.0)
    assert took < 3.5
    assert repo_closed == [1]
    messages = [r.getMessage() for r in caplog.records]
    assert any("dropped 1 held alert" in m for m in messages)
    stuck = [
        r.getMessage() for r in caplog.records
        if r.levelno == logging.WARNING and "did not finish" in r.getMessage()
    ]
    assert len(stuck) == 1 and "mqtt" in stuck[0]
    assert "engine stopped" in messages


def test_interrupt_inside_alerts_shutdown_still_closes_repo():
    def interrupted(*a, **k) -> None:
        raise KeyboardInterrupt

    repo_closed: list = []
    closed: list = []
    ctx = SimpleNamespace(
        pipeline=SimpleNamespace(stop=lambda: None),
        repo=SimpleNamespace(close=lambda: repo_closed.append(1)),
        shutdown=threading.Event(),
        alerts=SimpleNamespace(stop=interrupted),
        notifiers=[SimpleNamespace(name="desktop", close=lambda: closed.append(1))],
    )
    app_mod.shutdown_engine(ctx)
    assert repo_closed == [1]
    assert closed == [1]  # the other notifiers are still closed


def test_interrupt_while_stopping_alerts_still_closes_repo(monkeypatch):
    def interrupted(ctx) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(app_mod, "_stop_alerts", interrupted)
    repo_closed: list = []
    ctx = SimpleNamespace(
        pipeline=SimpleNamespace(stop=lambda: None),
        repo=SimpleNamespace(close=lambda: repo_closed.append(1)),
        shutdown=threading.Event(),
        alerts=None,
        notifiers=[],
    )
    app_mod.shutdown_engine(ctx)  # a second Ctrl+C there is swallowed, like during the pipeline stop
    assert repo_closed == [1]


def test_failing_alerts_shutdown_still_closes_repo(monkeypatch):
    def broken(ctx) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(app_mod, "_stop_alerts", broken)
    repo_closed: list = []
    ctx = SimpleNamespace(
        pipeline=SimpleNamespace(stop=lambda: None),
        repo=SimpleNamespace(close=lambda: repo_closed.append(1)),
        shutdown=threading.Event(),
    )
    with pytest.raises(RuntimeError):
        app_mod.shutdown_engine(ctx)
    assert repo_closed == [1]


def test_dotenv_inline_comments(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "SKY_E=value # comment\n"
        'SKY_F="quoted # kept"\n'
        "SKY_G='single # kept'  # trailing comment\n"
        "SKY_H=a#b\n"
        "SKY_I=tab\t# comment\n",
        encoding="utf-8",
    )
    for k in ("SKY_E", "SKY_F", "SKY_G", "SKY_H", "SKY_I"):
        monkeypatch.delenv(k, raising=False)
    load_dotenv(tmp_path / ".env")
    assert os.environ["SKY_E"] == "value"
    assert os.environ["SKY_F"] == "quoted # kept"
    assert os.environ["SKY_G"] == "single # kept"
    assert os.environ["SKY_H"] == "a#b"  # no space before #: part of the value
    assert os.environ["SKY_I"] == "tab"
    for k in ("SKY_E", "SKY_F", "SKY_G", "SKY_H", "SKY_I"):
        monkeypatch.delenv(k)


def test_startup_summary_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    video = make_video(tmp_path / "v.mp4", 5)
    cfg = load_config(write_config(tmp_path, source=f"  path: {video.as_posix()}"))
    with caplog.at_level("INFO", logger="engine.app"):
        ctx = build_engine(cfg)
    try:
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "v.mp4" in text and "320x240" in text and "30.00 fps" in text
        assert "RF-DETR small" in text and "device cpu" in text
        assert str(cfg.evidence.data_dir) in text
    finally:
        ctx.pipeline.stop()
        ctx.repo.close()


def test_uvicorn_logs_reach_rotating_file(tmp_path):
    import logging

    from engine.logging_setup import setup_logging
    from tests.test_api import make_rig

    import uvicorn

    from engine.api.app import create_app

    rig = make_rig(tmp_path / "data")
    # uvicorn's default log config (what 0.1.0 used) keeps its loggers to itself
    uvicorn.Config(create_app(rig.ctx), log_level="info")
    log_path = setup_logging(tmp_path / "data")
    try:
        app_mod.build_server(rig.ctx)  # configures uvicorn logging as main() does
        logging.getLogger("uvicorn.error").info("uvicorn says hi")
        logging.getLogger("uvicorn.access").info('127.0.0.1:1 - "GET /x HTTP/1.1" 200')
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = log_path.read_text(encoding="utf-8")
        assert "uvicorn says hi" in text
        assert '"GET /x HTTP/1.1" 200' in text
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_sky_eye", False):
                root.removeHandler(handler)
                handler.close()
        rig.repo.close()


@pytest.mark.parametrize(
    ("source_yaml", "secret", "kind", "name"),
    [
        (
            "  type: stream\n  url: rtsp://pilot:s3cret@10.0.0.5:8554/live?token=t0ken\n",
            ["s3cret", "t0ken"],
            "stream",
            "rtsp://***@10.0.0.5:8554/live?***",
        ),
        (
            "  type: go2rtc\n  go2rtc:\n    name: drone\n"
            "    rtsp_base: rtsp://admin:hunter2@127.0.0.1:8554\n",
            ["hunter2"],
            "go2rtc",
            "go2rtc:drone",
        ),
    ],
)
def test_stream_credentials_not_stored_or_served(tmp_path, monkeypatch, source_yaml, secret, kind, name):
    import sqlite3

    import numpy as np
    from fastapi.testclient import TestClient

    from engine.api.app import create_app
    from engine.sources import StreamSource

    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    urls = []

    def fake_source(scfg):  # no network: one frame from a fake opener
        url = scfg.stream_url()
        urls.append(url)
        return StreamSource(url, opener=lambda u, o: iter([np.zeros((24, 32, 3), np.uint8)]))

    monkeypatch.setattr(app_mod, "build_source", fake_source)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        f"source:\n{source_yaml}detector:\n  device: cpu\nevidence:\n  data_dir: data\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    ctx = build_engine(cfg)
    try:
        assert any(s in urls[0] for s in secret)  # the source itself gets the real URL
        assert ctx.info["source"]["type"] == kind and ctx.info["source"]["name"] == name
        body = TestClient(create_app(ctx)).get("/api/info").text
    finally:
        ctx.pipeline.stop()
        ctx.repo.close()
    with sqlite3.connect(cfg.evidence.data_dir / "sky-eye.db") as db:
        label, config_json = db.execute("SELECT source, config_json FROM runs").fetchone()
    assert label == name
    assert '"type":"' + kind + '"' in config_json.replace(" ", "")
    for s in secret:
        assert s not in config_json and s not in body and s not in label
    assert name in body


class _FakeNotifier:
    def __init__(self, name):
        self.name = name
        self.closed = 0

    def send(self, batch):
        pass

    def close(self):
        self.closed += 1


def test_build_engine_starts_and_stops_dispatcher(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-secret-1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat-secret-2")
    monkeypatch.setenv("MQTT_PASSWORD", "pw-secret-3")
    built: dict = {}

    def fake_telegram(token, chat_id, media_root, dashboard_url=None, **k):
        built["telegram"] = (token, chat_id, Path(media_root), dashboard_url)
        return _FakeNotifier("telegram")

    def fake_mqtt(host, port, topic, username, password, tls, **k):
        built["mqtt"] = (host, port, topic, username, password, tls)
        built["mqtt_obj"] = _FakeNotifier("mqtt")
        return built["mqtt_obj"]

    monkeypatch.setattr(app_mod, "TelegramNotifier", fake_telegram)
    monkeypatch.setattr(app_mod, "MqttNotifier", fake_mqtt)
    monkeypatch.setattr(app_mod, "DesktopNotifier", lambda: _FakeNotifier("desktop"))
    video = make_video(tmp_path / "v.mp4", 5)
    extra = (
        "alerts:\n  cooldown_s: 3\n  telegram:\n    enabled: true\n"
        "    dashboard_url: http://box:8000\n"
        "  mqtt:\n    enabled: true\n    host: broker\n    username: bob\n"
    )
    cfg = load_config(write_config(tmp_path, source=f"  path: {video.as_posix()}", extra=extra))
    ctx = build_engine(cfg)
    try:
        assert ctx.info["alerts"] == ["desktop", "telegram", "mqtt"]
        assert ctx.runtime.alerts == {"desktop": True, "telegram": True, "mqtt": True}
        assert built["telegram"] == (
            "tok-secret-1", "chat-secret-2", cfg.evidence.data_dir / "media", "http://box:8000")
        assert built["mqtt"] == ("broker", 1883, "sky-eye/events", "bob", "pw-secret-3", False)
        assert ctx.alerts is not None
        assert not ctx.alerts._alive()  # built, not started
        ctx.alerts.start()
        assert ctx.alerts._alive()
    finally:
        app_mod.shutdown_engine(ctx)
    assert not ctx.alerts._alive()
    assert built["mqtt_obj"].closed == 1


def test_mqtt_password_ignored_without_username(tmp_path, monkeypatch):
    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    monkeypatch.setenv("MQTT_PASSWORD", "pw-secret-3")
    seen: dict = {}

    def fake_mqtt(host, port, topic, username, password, tls, **k):
        seen["password"] = password
        return _FakeNotifier("mqtt")

    monkeypatch.setattr(app_mod, "MqttNotifier", fake_mqtt)
    video = make_video(tmp_path / "v.mp4", 5)
    extra = "alerts:\n  desktop: false\n  mqtt:\n    enabled: true\n"
    cfg = load_config(write_config(tmp_path, source=f"  path: {video.as_posix()}", extra=extra))
    ctx = build_engine(cfg)
    try:
        assert seen["password"] is None
        assert ctx.runtime.alerts == {"mqtt": True}
    finally:
        app_mod.shutdown_engine(ctx)


def test_info_never_contains_secrets(tmp_path, monkeypatch):
    import sqlite3

    from fastapi.testclient import TestClient

    from engine.api.app import create_app

    monkeypatch.setattr(app_mod, "build_detector", lambda cfg: FakeDetector(lambda i: []))
    secrets = {"TELEGRAM_BOT_TOKEN": "tok-secret-1", "TELEGRAM_CHAT_ID": "chat-secret-2",
               "MQTT_PASSWORD": "pw-secret-3"}
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(app_mod, "TelegramNotifier", lambda *a, **k: _FakeNotifier("telegram"))
    monkeypatch.setattr(app_mod, "MqttNotifier", lambda *a, **k: _FakeNotifier("mqtt"))
    video = make_video(tmp_path / "v.mp4", 5)
    extra = ("alerts:\n  telegram:\n    enabled: true\n"
             "  mqtt:\n    enabled: true\n    username: bob\n")
    cfg = load_config(write_config(tmp_path, source=f"  path: {video.as_posix()}", extra=extra))
    ctx = build_engine(cfg)
    try:
        with TestClient(create_app(ctx)) as client:
            bodies = [client.get("/api/info").text, client.get("/api/config/runtime").text]
        db = sqlite3.connect(cfg.evidence.data_dir / "sky-eye.db")
        try:
            rows = db.execute("SELECT config_json FROM runs").fetchall()
        finally:
            db.close()
        assert rows
        bodies += [r[0] for r in rows]
        for body in bodies:
            for value in secrets.values():
                assert value not in body
    finally:
        app_mod.shutdown_engine(ctx)
