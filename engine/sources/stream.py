"""Live network video (RTSP, RTMP, UDP, HTTP) decoded by PyAV on a background thread."""
from __future__ import annotations

import functools
import logging
import math
import re
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import numpy as np

from engine.core import DEFAULT_FPS, Frame
from engine.sources.backoff import Backoff

log = logging.getLogger(__name__)

# For every stream: no input buffering, low-delay decoding, at most 0.5 s of
# demuxer reordering delay (microseconds).
LOW_LATENCY_OPTIONS: dict[str, str] = {
    "fflags": "nobuffer",
    "flags": "low_delay",
    "max_delay": "500000",
}
_RTSP_SCHEMES = frozenset({"rtsp", "rtsps"})
# These get FFmpeg's generic URL read/write timeout ("rw_timeout"). Not "timeout":
# for rtmp that option means "listen for an incoming connection".
_RW_TIMEOUT_SCHEMES = frozenset({"rtmp", "rtmps", "udp", "http", "https"})
_CLOSE_JOIN_S = 1.5  # close() waits this long for the reader (it must return within 2 s)
_OPEN_WAIT_MARGIN_S = 1.0  # open() waits for the first attempt: open timeout + this
_MIN_FPS, _MAX_FPS = 1.0, 240.0  # a stream rate outside this is not trusted


def _scheme(url: str) -> str:
    try:
        return urlsplit(url).scheme.lower()
    except ValueError:  # e.g. a broken IPv6 host
        return ""


def stream_options(url: str, read_timeout_s: float) -> dict[str, str]:
    """FFmpeg input options for `url`: low latency, TCP for RTSP, an I/O timeout."""
    opts = dict(LOW_LATENCY_OPTIONS)
    scheme = _scheme(url)
    micros = str(round(read_timeout_s * 1_000_000))
    if scheme in _RTSP_SCHEMES:
        opts["rtsp_transport"] = "tcp"
        opts["timeout"] = micros  # RTSP socket I/O timeout (FFmpeg >= 5; was "stimeout")
    elif scheme in _RW_TIMEOUT_SCHEMES:
        opts["rw_timeout"] = micros
    return opts


_URL_START = r"[A-Za-z][A-Za-z0-9+.\-]*://"
# scheme:// up to the LAST "@" on the line: a password may hold any character
# (#, /, ?, @, quotes, backslashes), so the raw text is matched, not urlsplit's view
_USERINFO_RE = re.compile(rf"({_URL_START})[^\n]+@")
# a query string after scheme://host/path, up to a quote or whitespace
_QUERY_RE = re.compile(rf"({_URL_START}[^\s'\"?]*)\?[^\s'\"]*")


def redact_url(text: str) -> str:
    """`text` (a URL, or a message quoting URLs) with any user:password and query
    string replaced by ***, for logs, the database and the API."""
    text = _USERINFO_RE.sub(r"\1***@", text)
    return _QUERY_RE.sub(r"\1?***", text)


def _describe(exc: BaseException) -> str:
    """An exception for the log, with URLs redacted (PyAV quotes the URL)."""
    return redact_url(f"{type(exc).__name__}: {exc}")


class _PyAVStream:
    """The default opener's result: the first video stream of `url`, decoded to
    BGR arrays. Yields None for a packet that gave no picture, so the reader
    can apply its no-frame timeout."""

    def __init__(
        self, url: str, options: dict[str, str], open_timeout_s: float, read_timeout_s: float
    ) -> None:
        import av  # deferred: loads FFmpeg

        self._invalid_data = av.error.InvalidDataError
        # timeout=(open, read): PyAV's interrupt callback aborts a connect or a
        # demux call that blocks longer, whatever the protocol.
        self._container = av.open(
            url, options=options, timeout=(open_timeout_s, read_timeout_s)
        )
        try:
            self._video = self._container.streams.video[0]
        except IndexError:
            self._container.close()
            raise RuntimeError("the stream has no video track") from None
        rate = self._video.average_rate or self._video.guessed_rate
        self.fps: float | None = float(rate) if rate else None

    def __iter__(self) -> Iterator[np.ndarray | None]:
        # Demux every stream: demux(video) would skip audio/data packets
        # internally, so video stopping while audio flows would never reach the
        # reader's no-frame timeout.
        for packet in self._container.demux():
            if packet.stream_index != self._video.index:
                yield None
                continue
            try:
                frames = packet.decode()
            except self._invalid_data:
                frames = []  # a corrupt packet: skip it (the no-frame timeout still applies)
            if not frames:
                yield None
            for frame in frames:
                yield frame.to_ndarray(format="bgr24")

    def close(self) -> None:
        self._container.close()


def open_pyav(
    url: str, options: dict[str, str], *, open_timeout_s: float, read_timeout_s: float
) -> _PyAVStream:
    return _PyAVStream(url, options, open_timeout_s, read_timeout_s)


def _valid_fps(value: Any) -> float | None:
    try:
        fps = float(value)
    except (TypeError, ValueError):
        return None
    return fps if math.isfinite(fps) and _MIN_FPS <= fps <= _MAX_FPS else None


class StreamSource:
    """A live network stream. A background thread connects, decodes and keeps
    only the newest frame; read() hands it out once and never blocks.

    state is "reconnecting" from open() until a frame arrives, then "running";
    failed_attempts counts the connection attempts that failed since the last
    frame (0 while the first attempt is still connecting). The source never ends: on a connect failure, a read error, the stream
    ending or no frame for `read_timeout_s` (for the first frame of a
    connection: max(read_timeout_s, open_timeout_s), so a long-GOP camera can
    reach its first keyframe), state turns "reconnecting" again and the thread
    retries after 1, 2, 4 ... 30 s; the first good frame makes it "running" and
    resets the delay. fps is the stream's rate when it lies in 1..240, else
    DEFAULT_FPS.

    `opener(url, options)` connects and returns an iterable of BGR arrays (None =
    no picture yet) or raises; an optional `fps` attribute gives the stream rate
    and an optional `close()` is called when the reader is done with it. The
    default wraps PyAV. `sleep(delay)` waits between attempts; the default waits
    on the stop signal, so close() is never held up by a 30 s delay.

    The URL is not validated here (SourceConfig does that); any string FFmpeg
    can open works, including a local file path.
    """

    def __init__(
        self,
        url: str,
        *,
        open_timeout_s: float = 10.0,
        read_timeout_s: float = 5.0,
        opener: Callable[[str, dict], Any] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._url = url
        self._open_timeout_s = open_timeout_s
        self._read_timeout_s = read_timeout_s
        self._first_frame_timeout_s = max(read_timeout_s, open_timeout_s)
        self._options = stream_options(url, read_timeout_s)
        self._opener = opener or functools.partial(
            open_pyav, open_timeout_s=open_timeout_s, read_timeout_s=read_timeout_s
        )
        self._clock = clock
        self._sleep = sleep
        self._backoff = Backoff()  # reader thread only
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._index = 0  # read() caller only
        self._t0 = 0.0
        self._fps = DEFAULT_FPS
        # guarded by _lock
        self._lock = threading.Lock()
        self._latest: tuple[np.ndarray, float, datetime] | None = None
        self._state = "reconnecting"  # until the first frame
        self._failed_attempts = 0  # since the last frame
        self._size: tuple[int, int] | None = None

    # ----- VideoSource -----

    def open(self) -> None:
        """Start the reader and wait (at most the open timeout + 1 s) for its first
        connection attempt, so fps is the stream's own. State stays
        "reconnecting" until the first frame; an unreachable stream does not
        raise."""
        if self._thread is not None:
            raise RuntimeError("stream source is already open")
        self._stop = stop = threading.Event()
        attempted = threading.Event()
        with self._lock:
            self._latest = None
            self._state = "reconnecting"
            self._failed_attempts = 0
        self._index = 0
        self._backoff.reset()
        self._t0 = self._clock()
        self._thread = threading.Thread(
            target=self._run, args=(stop, attempted), name="sky-eye-stream", daemon=True
        )
        self._thread.start()
        attempted.wait(self._open_timeout_s + _OPEN_WAIT_MARGIN_S)

    def read(self) -> Frame | None:
        with self._lock:
            latest, self._latest = self._latest, None
        if latest is None:
            return None
        image, ts, wall_time = latest
        frame = Frame(image, ts, wall_time, self._index)
        self._index += 1
        return frame

    def close(self) -> None:
        """Stop the reader; returns within 2 s. A reader stuck in network I/O
        (bounded by the read timeout) is left to finish on its own."""
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._stop.set()
        thread.join(_CLOSE_JOIN_S)
        if thread.is_alive():
            log.warning("video stream reader still busy after %.1f s; it stops on its own",
                        _CLOSE_JOIN_S)
        with self._lock:
            self._latest = None

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def ended(self) -> bool:
        return False

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def failed_attempts(self) -> int:
        """Connection attempts that failed since the last frame (0 = none yet)."""
        with self._lock:
            return self._failed_attempts

    @property
    def url(self) -> str:
        return self._url

    @property
    def frame_size(self) -> tuple[int, int] | None:
        """(width, height) of the newest decoded frame, or None before the first."""
        with self._lock:
            return self._size

    # ----- reader thread -----

    def _run(self, stop: threading.Event, attempted: threading.Event) -> None:
        shown = redact_url(self._url)
        failures = 0  # failed attempts since the stream last ran
        while not stop.is_set():
            try:
                self._read_stream(stop, attempted)
                reason = "the stream ended"
            except Exception as exc:  # noqa: BLE001 - any failure means: reconnect
                reason = _describe(exc)
            if stop.is_set():
                break
            with self._lock:
                was_running = self._state == "running"
                self._state = "reconnecting"
                self._failed_attempts += 1
            attempted.set()
            failures = 1 if was_running else failures + 1
            delay = self._backoff.next_delay()
            if failures == 1:
                log.warning("video stream %s unavailable (%s); reconnecting in %g s",
                            shown, reason, delay)
            else:
                log.info("video stream %s still unavailable (%s); attempt %d, next in %g s",
                         shown, reason, failures, delay)
            if self._sleep is None:
                stop.wait(delay)
            else:
                self._sleep(delay)

    def _read_stream(self, stop: threading.Event, attempted: threading.Event) -> None:
        """One connection: publish frames until an error, the end or stop."""
        stream = self._opener(self._url, dict(self._options))
        items: Any = stream
        try:
            items = iter(stream)
            fps = _valid_fps(getattr(stream, "fps", None))
            self._fps = DEFAULT_FPS if fps is None else fps
            attempted.set()
            last_frame = self._clock()
            limit = self._first_frame_timeout_s  # then read_timeout_s
            for image in items:
                if stop.is_set():
                    return
                now = self._clock()
                if image is None:
                    if now - last_frame > limit:
                        raise TimeoutError(f"no video frame for {limit:g} s")
                    continue
                last_frame = now
                limit = self._read_timeout_s
                self._publish(image, now, stop)
        finally:
            # the iterator first (a PyAV demux generator), then the container
            closers = [getattr(items, "close", None)] if items is not stream else []
            closers.append(getattr(stream, "close", None))
            for close in closers:
                if callable(close):
                    try:
                        close()
                    except Exception as exc:  # noqa: BLE001 - already reconnecting or stopping
                        log.debug("closing the video stream failed: %s", _describe(exc))

    def _publish(self, image: np.ndarray, now: float, stop: threading.Event) -> None:
        height, width = image.shape[:2]
        with self._lock:
            if stop.is_set():  # a reader that close() gave up on
                return
            self._latest = (image, now - self._t0, datetime.now(timezone.utc))
            self._size = (width, height)
            recovered = self._state != "running"
            self._state = "running"
            self._failed_attempts = 0
        self._backoff.reset()
        if recovered:
            log.info("video stream %s: receiving frames", redact_url(self._url))
