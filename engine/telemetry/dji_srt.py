"""DJI .SRT flight-log telemetry for recorded flights."""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from engine.core import Frame
from engine.telemetry.core import DronePose, PoseBuffer

log = logging.getLogger(__name__)

_NUM = r"(-?\d+(?:\.\d+)?)"
_TIME = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)"
)
_TAG = re.compile(r"<[^>]*>")
_BRACKET = re.compile(r"\[([^\]]*)\]")
_PAIR = re.compile(r"(\w+)\s*:\s*" + _NUM)
_GPS = re.compile(r"GPS\(\s*" + _NUM + r"\s*,\s*" + _NUM + r"\s*,\s*" + _NUM + r"\s*\)")
_H = re.compile(r"(?<![A-Za-z])H\s*" + _NUM + r"\s*m\b")
_BARO = re.compile(r"BAROMETER:\s*" + _NUM)


def _secs(h: str, m: str, s: str, frac: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) / 10 ** len(frac)


def _parse(text: str, pitch_deg: float | None = None) -> tuple[list[DronePose], float]:
    """Poses plus the end time of the last subtitle block. `pitch_deg` is the camera
    pitch to use when a block has none (None: assume straight down, flagged)."""
    matches = list(_TIME.finditer(text))
    poses: list[DronePose] = []
    noted: set[str] = set()

    def note_once(key: str, msg: str) -> None:
        if key not in noted:
            noted.add(key)
            log.info(msg)

    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = _TAG.sub("", text[m.end():end])
        vals: dict[str, float] = {}
        for br in _BRACKET.findall(body):
            for k, v in _PAIR.findall(br):
                vals.setdefault(k.lower(), float(v))
        lat, lon = vals.get("latitude"), vals.get("longitude")
        alt = vals.get("rel_alt")
        gps = _GPS.search(body)
        if (lat is None or lon is None) and gps:
            lon, lat = float(gps.group(1)), float(gps.group(2))
        if alt is None:
            h = _H.search(body) or _BARO.search(body)
            alt = float(h.group(1)) if h else None
        if lat is None or lon is None or alt is None:
            continue
        yaw = vals.get("yaw", vals.get("gb_yaw"))
        yaw_assumed = yaw is None
        if yaw is None:
            note_once("yaw", "SRT has no yaw; assuming 0 (north), locations get a wider error")
            yaw = 0.0
        pitch = vals.get("gb_pitch")
        pitch_assumed = False
        if pitch is None and pitch_deg is not None:
            note_once("pitch", f"SRT has no gimbal pitch; using camera.pitch_deg ({pitch_deg:g})")
            pitch = pitch_deg
        elif pitch is None:
            note_once("pitch", "SRT has no gimbal pitch; assuming straight down (-90), "
                      "locations are unreliable (set camera.pitch_deg if the angle is fixed)")
            pitch, pitch_assumed = -90.0, True
        poses.append(DronePose(
            ts=_secs(*m.group(1, 2, 3, 4)), lat=lat, lon=lon, alt_agl_m=alt,
            yaw_deg=yaw, cam_pitch_deg=pitch, cam_roll_deg=vals.get("gb_roll", 0.0),
            pitch_assumed=pitch_assumed, yaw_assumed=yaw_assumed,
        ))
    period = _secs(*matches[-1].group(5, 6, 7, 8)) if matches else 0.0
    return poses, period


def parse_srt(text: str, pitch_deg: float | None = None) -> list[DronePose]:
    """Poses from DJI SRT text (new `[key: value]` or legacy `GPS(lon,lat,x)`)."""
    return _parse(text, pitch_deg)[0]


class DjiSrtTelemetry:
    """Recorded-flight telemetry; `latest`/`track` follow the last looked-up frame."""

    def __init__(
        self, path: Path, max_age_s: float, loop: bool = False, pitch_deg: float | None = None
    ) -> None:
        self._path = Path(path)
        self._max_age_s = max_age_s
        self._loop = loop
        self._pitch_deg = pitch_deg  # camera.pitch_deg: used when the log has no gimbal pitch
        self._buffer = PoseBuffer(max_age_s)
        self._period = 0.0
        self._started = False  # parsing finished
        self._loader: threading.Thread | None = None
        self._count = 0
        self._last_t: float | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        """Parse the file now, on the calling thread. Idempotent."""
        with self._lock:
            if self._started:
                return
        self._parse_file()

    def start(self) -> None:
        """Already loaded: nothing to do. Otherwise parse on a helper thread, and the
        state stays "waiting" until it is done."""
        with self._lock:
            if self._started or self._loader is not None:
                return
            self._loader = threading.Thread(
                target=self._parse_file, name="sky-eye-srt-load", daemon=True
            )
        self._loader.start()

    def _parse_file(self) -> None:
        poses: list[DronePose] = []
        period = 0.0
        try:
            text = self._path.read_text(encoding="utf-8-sig", errors="replace")
            poses, period = _parse(text, self._pitch_deg)
        except Exception as e:  # telemetry problems must never stop detection
            log.warning("cannot read SRT file %s: %s", self._path, e)
        buffer = PoseBuffer(self._max_age_s, capacity=max(len(poses), 1))
        for p in poses:
            buffer.add(p)
        with self._lock:
            self._buffer, self._period, self._count = buffer, period, len(poses)
            self._started = True

    def stop(self) -> None:
        loader = self._loader
        if loader is not None and loader is not threading.current_thread():
            loader.join(1.0)

    def pose_for(self, frame: Frame) -> DronePose | None:
        t = frame.ts
        if self._loop and self._period > 0:
            t = t % self._period
        with self._lock:
            self._last_t = t
            buffer = self._buffer
        return buffer.nearest(t)

    def latest(self) -> DronePose | None:
        with self._lock:
            t, buffer = self._last_t, self._buffer
        return None if t is None else buffer.nearest(t)

    def track(self, seconds: float) -> list[DronePose]:
        with self._lock:
            t, buffer = self._last_t, self._buffer
        if t is None:
            return []
        return [p for p in buffer.track(t - seconds) if p.ts <= t]

    @property
    def state(self) -> str:
        with self._lock:
            if not self._started:
                return "waiting"
            return "ok" if self._count else "stale"
