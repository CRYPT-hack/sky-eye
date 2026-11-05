"""Console + rotating file logging."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 3
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def setup_logging(data_dir: Path) -> Path:
    """Log to the console and to <data_dir>/logs/sky-eye.log (5 MB x 3)."""
    log_dir = Path(data_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "sky-eye.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        if getattr(handler, "_sky_eye", False):
            root.removeHandler(handler)
            handler.close()
    formatter = logging.Formatter(_FORMAT)
    console = logging.StreamHandler()
    file = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    for handler in (console, file):
        handler.setFormatter(formatter)
        handler._sky_eye = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    # uvicorn runs with log_config=None; make sure its loggers (status, errors,
    # access log) end up in the console and the rotating file like ours, even
    # if something configured them before.
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.propagate = True
    return log_path
