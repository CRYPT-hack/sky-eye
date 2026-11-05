from __future__ import annotations

import os
import signal
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

import engine.app as app_mod
from engine.app import build_engine, load_dotenv, main
from engine.config import load_config
from engine.detectors.fake import FakeDetector
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
    ctx = build_engine(cfg)
    try:
        assert ctx.pipeline.status().source_state == "starting"
        assert ctx.repo.count_trucks(1) == 0  # run 1 exists
        assert ctx.data_dir == cfg.evidence.data_dir
        assert ctx.token is None
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
