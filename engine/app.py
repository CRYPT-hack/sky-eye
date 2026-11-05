"""Wires the engine together and runs it: `python -m engine --config config.yaml`."""
from __future__ import annotations

import argparse
import logging
import os
import re
import socket
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import FrameType

import uvicorn

from engine.api.app import EngineContext, create_app
from engine.config import AppConfig, ConfigError, load_config
from engine.detectors.rfdetr import DetectorError, build_detector
from engine.events.bus import EventBus
from engine.events.evidence import EvidenceRecorder
from engine.events.manager import EventManager
from engine.logging_setup import setup_logging
from engine.pipeline import Pipeline, RuntimeSettings
from engine.sources import build_source
from engine.storage import TruckRepository, media_dir, remove_partial_clips
from engine.tracking import build_tracker

log = logging.getLogger(__name__)

_STOP_TIMEOUT_S = 5.0
# Backstop: uvicorn cancels responses still open this long after an exit request.
_GRACEFUL_SHUTDOWN_S = 3
_INLINE_COMMENT = re.compile(r"\s+#.*$")
_hard_exit = os._exit  # replaced in tests


def _dotenv_value(raw: str) -> str:
    """Unquote a .env value; drop a ` # comment` after an unquoted value."""
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        if end != -1:
            return raw[1:end]  # anything after the closing quote is a comment
    return _INLINE_COMMENT.sub("", raw)


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding existing variables."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")  # tolerate a BOM
    except FileNotFoundError:
        return
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), _dotenv_value(value.strip())
        if not key:
            continue
        os.environ.setdefault(key, value)


def build_engine(cfg: AppConfig) -> EngineContext:
    """Build every component. The detector (slow model load) comes first; if
    anything after the source is opened fails, the source is closed again."""
    detector = build_detector(cfg.detector)
    log.info(
        "detector: RF-DETR %s, input %d px, device %s, confidence %.2f, classes %s",
        cfg.detector.size,
        cfg.detector.input_size,
        cfg.detector.device,
        cfg.detector.confidence,
        ", ".join(cfg.detector.classes),
    )
    data_dir = cfg.evidence.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    source = build_source(cfg.source)
    repo: TruckRepository | None = None
    try:
        try:
            source.open()
        except (RuntimeError, OSError) as exc:
            if cfg.source.type == "file":
                where = f"source.path {cfg.source.path}"
            else:
                where = f"source.device {cfg.source.device}"
            raise ConfigError(f"cannot open video source ({where}): {exc}") from exc
        label = str(cfg.source.path) if cfg.source.type == "file" else f"capture:{cfg.source.device}"
        size = getattr(source, "frame_size", None)
        log.info(
            "source: %s, %s @ %.2f fps, realtime %s",
            label,
            f"{size[0]}x{size[1]}" if size else "unknown size",
            source.fps,
            "on" if cfg.source.realtime else "off",
        )
        log.info("data dir: %s (media limit %.1f GB)", data_dir, cfg.evidence.max_disk_gb)
        tracker = build_tracker(cfg.tracker, source.fps)
        recorder = EvidenceRecorder(
            data_dir, cfg.evidence.clip_before_s, cfg.evidence.clip_after_s
        )
        repo = TruckRepository(data_dir / "sky-eye.db")
        run_id = repo.create_run(label, cfg.model_dump_json())
        remove_partial_clips(media_dir(data_dir))
        bus = EventBus()
        manager = EventManager(
            repo,
            recorder,
            bus,
            run_id,
            data_dir,
            cfg.evidence.snapshot_window_s,
            int(cfg.evidence.max_disk_gb * 1024**3),
        )
        runtime = RuntimeSettings(confidence=cfg.detector.confidence)
        pipeline = Pipeline(
            source,
            detector,
            tracker,
            manager,
            recorder,
            runtime,
            realtime=cfg.source.realtime,
            stream_max_width=cfg.api.stream_max_width,
        )
    except BaseException:
        try:
            source.close()
        finally:
            if repo is not None:
                repo.close()
        raise
    return EngineContext(
        pipeline=pipeline,
        repo=repo,
        bus=bus,
        runtime=runtime,
        data_dir=data_dir,
        api=cfg.api,
        token=os.environ.get("SKYEYE_API_TOKEN"),
    )


class EngineServer(uvicorn.Server):
    """uvicorn server that starts the engine once it is listening and tells the
    app when it is stopping, so open MJPEG/WebSocket responses end at once."""

    def __init__(
        self,
        config: uvicorn.Config,
        shutdown: threading.Event,
        on_started: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(config)
        self._shutdown = shutdown
        self._on_started = on_started

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self._shutdown.set()
        super().handle_exit(sig, frame)
        # uvicorn re-raises the captured signal after run() returns; for SIGBREAK
        # (Ctrl+Break) or SIGTERM that kills the process before the pipeline is
        # stopped. main() stops everything itself, so let run() just return.
        captured = getattr(self, "_captured_signals", None)
        if isinstance(captured, list):
            captured.clear()

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started and not self.should_exit and self._on_started is not None:
            self._on_started()


def build_server(
    ctx: EngineContext, on_started: Callable[[], None] | None = None
) -> EngineServer:
    config = uvicorn.Config(
        create_app(ctx),
        host=ctx.api.host,
        port=ctx.api.port,
        log_level="info",
        log_config=None,  # uvicorn logs go to our handlers (console + rotating file)
        timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_S,
    )
    return EngineServer(config, shutdown=ctx.shutdown, on_started=on_started)


def _bind(host: str, port: int) -> socket.socket:
    """Take the HTTP port up front, so a busy port fails before anything runs."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        return socket.create_server((host, port), family=family, backlog=2048)
    except OSError as exc:
        raise ConfigError(
            f"cannot listen on {host}:{port} (api.host / api.port): {exc.strerror or exc}. "
            "Another program, or another sky-eye, may be using the port: stop it or "
            "change api.port."
        ) from exc


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # UDP: no packet is sent
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _dashboard_url(host: str, port: int) -> str:
    if host in ("", "0.0.0.0", "::"):
        host = _lan_ip()
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{port}/"


def _fail(message: str) -> int:
    print(" ".join(str(message).split()), file=sys.stderr)
    return 2


def _stop_pipeline(ctx: EngineContext) -> bool:
    """Stop the pipeline in a helper thread; give up after 5 s. True if it stopped."""
    stopper = threading.Thread(target=ctx.pipeline.stop, name="sky-eye-stop", daemon=True)
    stopper.start()
    stopper.join(_STOP_TIMEOUT_S)
    if stopper.is_alive():
        log.warning("pipeline did not stop within %.0f s; exiting anyway", _STOP_TIMEOUT_S)
        return False
    return True


def shutdown_engine(ctx: EngineContext) -> None:
    """Stop the pipeline and close the database. If the pipeline does not stop
    within 5 s, flush the logs and exit the process with code 1."""
    ctx.shutdown.set()
    try:
        clean = _stop_pipeline(ctx)
    except KeyboardInterrupt:  # another Ctrl+C while waiting: stop waiting
        clean = False
    if clean:
        ctx.repo.close()
        log.info("engine stopped")
        return
    logging.shutdown()
    _hard_exit(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine", description="sky-eye engine")
    parser.add_argument("--config", required=True, type=Path, help="path to config.yaml")
    parser.add_argument(
        "--check", action="store_true", help="validate the config and load the detector, then exit"
    )
    args = parser.parse_args(argv)

    config_path = args.config
    try:
        load_dotenv(config_path.resolve().parent / ".env")
        cfg = load_config(config_path)
        if args.check:
            build_detector(cfg.detector)
            print("OK")
            return 0
        setup_logging(cfg.evidence.data_dir)
        sock = _bind(cfg.api.host, cfg.api.port)
        try:
            ctx = build_engine(cfg)
        except BaseException:
            sock.close()
            raise
    except (ConfigError, DetectorError) as exc:
        return _fail(f"error: {exc}")

    url = _dashboard_url(cfg.api.host, cfg.api.port)

    def on_started() -> None:
        # only once the server is listening: the pipeline, then the URL
        ctx.pipeline.start()
        log.info("listening on %s:%d; dashboard at %s", cfg.api.host, cfg.api.port, url)
        print(f"Dashboard: {url}", flush=True)

    server = build_server(ctx, on_started)
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        pass
    except SystemExit:  # uvicorn exits like this when its startup fails
        if server.started:
            raise
    finally:
        sock.close()
        shutdown_engine(ctx)
    if not server.started:
        return _fail("error: the HTTP server failed to start; see the log above")
    return 0
