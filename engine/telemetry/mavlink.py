"""Live MAVLink telemetry (PX4/ArduPilot over UDP or serial)."""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Any, Callable

from engine.core import Frame
from engine.sources.backoff import Backoff
from engine.telemetry.connection import validate_connection
from engine.telemetry.core import TRACK_MIN_SPACING_S, DronePose, PoseBuffer

# GIMBAL_DEVICE_ATTITUDE_STATUS exists only in MAVLink 2; must be set before the import below.
os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil  # noqa: E402

log = logging.getLogger(__name__)

_RECV_TIMEOUT_S = 0.5
_STOP_JOIN_S = 1.5
_TRACK_CAPACITY = 1800  # 30 minutes at one pose per second
_HDG_UNKNOWN = 65535


def _scheme(connection: str) -> str:
    """Only the part before the first ':' - the rest may carry credentials."""
    return connection.split(":", 1)[0]


def _norm_yaw(deg: float) -> float:
    y = deg % 360.0
    return 0.0 if y >= 360.0 else y


class MavlinkTelemetry:
    """Poses from a MAVLink stream, stamped with wall-clock receive time."""

    def __init__(
        self,
        connection: str,
        max_age_s: float,
        stale_after_s: float,
        conn_factory: Callable[[str], Any] = mavutil.mavlink_connection,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        backoff_initial: float = 1.0,
    ) -> None:
        validate_connection(connection)  # UDP or serial only (a dead TCP link spins pymavlink)
        self._connection = connection
        self._stale_after_s = stale_after_s
        self._factory = conn_factory
        self._clock = clock
        self._monotonic = monotonic
        self._backoff = Backoff(initial=backoff_initial, maximum=max(30.0, backoff_initial))
        self._buffer = PoseBuffer(max_age_s, capacity=600)
        self._track = PoseBuffer(max_age_s, capacity=_TRACK_CAPACITY)
        self._last_track_ts: float | None = None
        self._last_pos_mono: float | None = None
        self._yaw_deg: float | None = None
        self._gimbal: tuple[float, float] | None = None
        self._noted_gimbal = False
        self._has_quaternion = False  # quaternion gimbal data wins over MOUNT_STATUS
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._conn: Any = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            if self._thread.is_alive():
                raise RuntimeError("MAVLink reader thread still running; cannot (re)start yet")
            self._thread = None
        self._stop = threading.Event()  # per-run, so an old thread can never be revived
        self._thread = threading.Thread(
            target=self._run, args=(self._stop,), name="mavlink", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=_STOP_JOIN_S)
            if not thread.is_alive():
                self._thread = None
        self._close_conn()

    def _close_conn(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass

    # -- reader thread -----------------------------------------------------
    def _run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                conn = self._factory(self._connection)
                self._conn = conn
                first = True
                while not stop.is_set():
                    msg = conn.recv_match(blocking=True, timeout=_RECV_TIMEOUT_S)
                    if msg is None:
                        continue
                    if first:
                        self._backoff.reset()
                        first = False
                    try:
                        self._handle(msg)
                    except Exception as e:  # noqa: BLE001 - a bad message is not a bad link
                        log.warning("mavlink message ignored: %s", type(e).__name__)
            except Exception as e:  # noqa: BLE001 - telemetry must never stop detection
                if stop.is_set():
                    break
                log.warning("MAVLink %s connection failed: %s", _scheme(self._connection),
                            type(e).__name__)
                self._close_conn()
                stop.wait(self._backoff.next_delay())
        self._close_conn()

    def _handle(self, msg: Any) -> None:
        kind = msg.get_type()
        if kind == "ATTITUDE":
            yaw = math.degrees(msg.yaw)
            if math.isfinite(yaw):
                self._yaw_deg = _norm_yaw(yaw)
        elif kind == "GIMBAL_DEVICE_ATTITUDE_STATUS":
            w, x, y, z = (float(v) for v in msg.q)
            if all(math.isfinite(v) for v in (w, x, y, z)):
                pitch = math.degrees(math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x)))))
                roll = math.degrees(math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y)))
                self._gimbal = (pitch, roll)
                self._has_quaternion = True
        elif kind == "MOUNT_STATUS":
            pitch, roll = msg.pointing_a / 100.0, msg.pointing_b / 100.0
            if not self._has_quaternion and math.isfinite(pitch) and math.isfinite(roll):
                self._gimbal = (pitch, roll)
        elif kind == "GLOBAL_POSITION_INT":
            self._on_position(msg)

    def _on_position(self, msg: Any) -> None:
        if msg.lat == 0 and msg.lon == 0:
            return  # ArduPilot reports 0,0 before a GPS fix: no fix, no pose
        lat, lon = msg.lat * 1e-7, msg.lon * 1e-7
        alt = msg.relative_alt / 1000.0
        if not all(math.isfinite(v) for v in (lat, lon, alt)):
            return
        now = self._clock()
        yaw = self._yaw_deg
        if yaw is None:
            yaw = _norm_yaw(msg.hdg / 100.0) if msg.hdg != _HDG_UNKNOWN else 0.0
        gimbal = self._gimbal
        if gimbal is None:
            if not self._noted_gimbal:
                self._noted_gimbal = True
                log.info("no MAVLink gimbal message yet; assuming camera straight down (-90)")
            gimbal = (-90.0, 0.0)
        pose = DronePose(
            ts=now, lat=lat, lon=lon, alt_agl_m=alt, yaw_deg=yaw,
            cam_pitch_deg=gimbal[0], cam_roll_deg=gimbal[1],
        )
        with self._lock:
            self._last_pos_mono = self._monotonic()  # before the pose becomes visible
            thin = (self._last_track_ts is None
                    or now - self._last_track_ts >= TRACK_MIN_SPACING_S)
            if thin:
                self._last_track_ts = now
        self._buffer.add(pose)
        if thin:
            self._track.add(pose)

    # -- Telemetry protocol --------------------------------------------------
    def pose_for(self, frame: Frame) -> DronePose | None:
        try:
            return self._buffer.nearest(frame.wall_time.timestamp())
        except Exception:  # noqa: BLE001
            return None

    def latest(self) -> DronePose | None:
        return self._buffer.latest()

    def track(self, seconds: float) -> list[DronePose]:
        last = self._buffer.latest()
        if last is None:
            return []
        return self._track.track(last.ts - seconds)

    @property
    def state(self) -> str:
        with self._lock:
            last = self._last_pos_mono
        if last is None:
            return "waiting"
        return "stale" if self._monotonic() - last > self._stale_after_s else "ok"
