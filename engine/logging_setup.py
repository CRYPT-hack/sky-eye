"""Console + rotating file logging."""
from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 3
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
# httpx logs every request URL at INFO; a Telegram URL carries the bot token
_QUIET_LOGGERS = ("httpx", "httpcore")
# a Telegram bot token in a URL: https://api.telegram.org/bot<digits>:<secret>/...
_BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_EXC_FORMATTER = logging.Formatter()


def redact_secrets(text: str) -> str:
    """`text` with any Telegram bot token replaced by `bot***`."""
    return _BOT_TOKEN_RE.sub("bot***", text)


class RedactingFilter(logging.Filter):
    """Rewrites a record's message and traceback so no bot token is written, whichever
    library logged it (defence in depth: notifiers redact their own errors already)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string: the handler reports it
            return True
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        if record.exc_info and not record.exc_text:
            record.exc_text = _EXC_FORMATTER.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_secrets(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_secrets(record.stack_info)
        return True


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
    redact = RedactingFilter()
    console = logging.StreamHandler()
    file = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8"
    )
    for handler in (console, file):
        handler.setFormatter(formatter)
        handler.addFilter(redact)
        handler._sky_eye = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # uvicorn runs with log_config=None; make sure its loggers (status, errors,
    # access log) end up in the console and the rotating file like ours, even
    # if something configured them before.
    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.propagate = True
    return log_path
