import math
import socket
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pytest

from engine.core import Frame
from engine.telemetry.mavlink import MavlinkTelemetry

DEADLINE_S = 5.0


class Msg:
    def __init__(self, kind, **fields):
        self._kind = kind
        self.__dict__.update(fields)

    def get_type(self):
        return self._kind


def gpi(lat=10.0, lon=20.0, rel_mm=50_000, hdg=9000):
    return Msg("GLOBAL_POSITION_INT", lat=int(lat * 1e7), lon=int(lon * 1e7),
               relative_alt=rel_mm, hdg=hdg)


def att(yaw_rad):
    return Msg("ATTITUDE", yaw=yaw_rad)


class FakeConn:
    """Yields scripted messages, then None (timeout) until closed."""

    def __init__(self, messages=()):
        self._q = list(messages)
        self._lock = threading.Lock()
        self.closed = threading.Event()

    def push(self, *msgs):
        with self._lock:
            self._q.extend(msgs)

    def recv_match(self, blocking=True, timeout=None):
        with self._lock:
            if self._q:
                return self._q.pop(0)
        time.sleep(0.01)
        return None

    def close(self):
        self.closed.set()


def wait_for(pred, timeout=DEADLINE_S):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def tel_factory():
    made = []

    def make(conn, clock=time.time, stale_after_s=2.0, max_age_s=0.5):
        t = MavlinkTelemetry("udpin:127.0.0.1:1", max_age_s, stale_after_s,
                             conn_factory=lambda c: conn, clock=clock)
        made.append(t)
        t.start()
        return t

    yield make
    for t in made:
        t.stop()


def test_combines_position_attitude_gimbal(tel_factory):
    conn = FakeConn([
        att(math.radians(90)),
        Msg("MOUNT_STATUS", pointing_a=-4500, pointing_b=300),
        gpi(lat=12.5, lon=77.25, rel_mm=42_500),
    ])
    t = tel_factory(conn)
    assert wait_for(lambda: t.latest() is not None)
    p = t.latest()
    assert p.lat == pytest.approx(12.5)
    assert p.lon == pytest.approx(77.25)
    assert p.alt_agl_m == pytest.approx(42.5)
    assert p.yaw_deg == pytest.approx(90.0)
    assert p.cam_pitch_deg == pytest.approx(-45.0)
    assert p.cam_roll_deg == pytest.approx(3.0)
    assert t.state == "ok"


def test_defaults_without_attitude_or_gimbal(tel_factory):
    conn = FakeConn([gpi(hdg=18000)])
    t = tel_factory(conn)
    assert wait_for(lambda: t.latest() is not None)
    p = t.latest()
    assert p.yaw_deg == pytest.approx(180.0)  # hdg fallback
    assert p.cam_pitch_deg == -90.0
    assert p.cam_roll_deg == 0.0


def test_negative_yaw_normalised(tel_factory):
    conn = FakeConn([att(math.radians(-90)), gpi()])
    t = tel_factory(conn)
    assert wait_for(lambda: t.latest() is not None)
    assert t.latest().yaw_deg == pytest.approx(270.0)


def test_quaternion_gimbal_to_pitch(tel_factory):
    half = math.radians(-60) / 2  # pitch -60 about y
    q = [math.cos(half), 0.0, math.sin(half), 0.0]
    conn = FakeConn([Msg("GIMBAL_DEVICE_ATTITUDE_STATUS", q=q), gpi()])
    t = tel_factory(conn)
    assert wait_for(lambda: t.latest() is not None)
    p = t.latest()
    assert p.cam_pitch_deg == pytest.approx(-60.0, abs=1e-6)
    assert p.cam_roll_deg == pytest.approx(0.0, abs=1e-6)


def test_mavlink_stale_and_recover(tel_factory):
    now = [1000.0]
    conn = FakeConn()
    t = tel_factory(conn, clock=lambda: now[0], stale_after_s=2.0)
    assert t.state == "waiting"
    assert t.latest() is None
    conn.push(gpi())
    assert wait_for(lambda: t.state == "ok")
    now[0] += 5.0
    assert t.state == "stale"
    assert t.latest() is not None  # last known position is kept
    conn.push(gpi(lat=11.0))
    assert wait_for(lambda: t.state == "ok")
    assert t.latest().lat == pytest.approx(11.0)


def test_pose_for_matches_wall_time(tel_factory):
    now = [1000.0]
    conn = FakeConn()
    t = tel_factory(conn, clock=lambda: now[0], max_age_s=0.5)
    conn.push(gpi(lat=1.0))
    assert wait_for(lambda: t.latest() is not None)
    now[0] = 1010.0
    conn.push(gpi(lat=2.0))
    assert wait_for(lambda: t.latest().lat == pytest.approx(2.0))

    def frame_at(ts):
        return Frame(np.zeros((2, 2, 3), np.uint8), 0.0,
                     datetime.fromtimestamp(ts, timezone.utc), 0)

    assert t.pose_for(frame_at(1000.2)).lat == pytest.approx(1.0)
    assert t.pose_for(frame_at(1009.9)).lat == pytest.approx(2.0)
    assert t.pose_for(frame_at(1005.0)) is None


def test_track_thinned_and_windowed(tel_factory):
    now = [0.0]
    conn = FakeConn()
    t = tel_factory(conn, clock=lambda: now[0], stale_after_s=1000)
    for i in range(10):
        now[0] = 100.0 + i * 0.4
        conn.push(gpi(lat=float(i)))
        assert wait_for(lambda i=i: t.latest() is not None
                        and t.latest().lat == pytest.approx(float(i)))
    track = t.track(300)
    assert [p.ts for p in track] == pytest.approx([100.0, 101.2, 102.4, 103.6])
    assert [p.ts for p in t.track(1.5)] == pytest.approx([102.4, 103.6])


def test_reconnects_after_error_without_leaking_connection_string(tel_factory, caplog):
    good = FakeConn([gpi()])
    calls = []

    def factory(c):
        calls.append(c)
        if len(calls) == 1:
            raise OSError("boom")
        return good

    import engine.telemetry.mavlink as m
    t = m.MavlinkTelemetry("udpin:user:secret@127.0.0.1:1", 0.5, 2.0,
                           conn_factory=factory, backoff_initial=0.05)
    with caplog.at_level("WARNING"):
        t.start()
        try:
            assert wait_for(lambda: t.state == "ok")
        finally:
            t.stop()
    assert len(calls) == 2
    assert "OSError" in caplog.text and "udpin" in caplog.text
    assert "secret" not in caplog.text
    assert good.closed.is_set()
    t.stop()  # idempotent


def test_udp_loopback_real_messages():
    from pymavlink import mavutil

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    t = MavlinkTelemetry(f"udpin:127.0.0.1:{port}", 0.5, 5.0)
    sender = mavutil.mavlink_connection(f"udpout:127.0.0.1:{port}", source_system=1)
    t.start()
    try:
        end = time.monotonic() + 10.0
        while time.monotonic() < end and t.state != "ok":
            sender.mav.attitude_send(0, 0.0, 0.0, math.radians(45), 0.0, 0.0, 0.0)
            sender.mav.global_position_int_send(
                0, int(47.5e7), int(8.25e7), 500_000, 30_000, 0, 0, 0, 9000)
            time.sleep(0.1)
        assert t.state == "ok"
        p = t.latest()
        assert p.lat == pytest.approx(47.5, abs=1e-5)
        assert p.lon == pytest.approx(8.25, abs=1e-5)
        assert p.alt_agl_m == pytest.approx(30.0)
        assert p.yaw_deg == pytest.approx(45.0, abs=0.01)
    finally:
        t.stop()
        sender.close()
