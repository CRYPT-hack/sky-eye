import threading
from datetime import datetime

import numpy as np
import pytest

from engine.core import Frame
from engine.telemetry import DronePose, NoTelemetry, PoseBuffer, Telemetry


def _pose(ts: float, lat: float = 1.0) -> DronePose:
    return DronePose(ts=ts, lat=lat, lon=2.0, alt_agl_m=50.0, yaw_deg=90.0, cam_pitch_deg=-90.0)


def test_pose_defaults_and_frozen():
    p = _pose(1.0)
    assert p.cam_roll_deg == 0.0
    with pytest.raises(AttributeError):
        p.lat = 5.0  # type: ignore[misc]


def test_nearest_within_and_outside_window():
    buf = PoseBuffer(max_age_s=0.5)
    buf.add(_pose(10.0))
    buf.add(_pose(11.0))
    assert buf.nearest(10.4).ts == 10.0
    assert buf.nearest(10.6).ts == 11.0
    assert buf.nearest(10.5).ts == 10.0  # tie: either neighbour is within the window
    assert buf.nearest(12.0) is None
    assert buf.nearest(9.0) is None


def test_nearest_empty_and_latest():
    buf = PoseBuffer()
    assert buf.nearest(1.0) is None
    assert buf.latest() is None
    buf.add(_pose(1.0))
    buf.add(_pose(2.0))
    assert buf.latest().ts == 2.0


def test_out_of_order_add_keeps_nearest_correct():
    buf = PoseBuffer(max_age_s=0.5)
    buf.add(_pose(10.0))
    buf.add(_pose(12.0))
    buf.add(_pose(11.0))  # late arrival
    assert buf.nearest(11.1).ts == 11.0
    assert buf.latest().ts == 12.0


def test_capacity_evicts_oldest_by_ts():
    buf = PoseBuffer(capacity=3)
    for t in (1.0, 2.0, 3.0, 4.0):
        buf.add(_pose(t))
    assert [p.ts for p in buf.track(0.0)] == [2.0, 3.0, 4.0]
    buf.add(_pose(3.5))  # between existing poses; the oldest (2.0) goes
    assert buf.nearest(2.0) is None
    assert buf.nearest(3.5).ts == 3.5 and buf.nearest(3.0).ts == 3.0


def test_track_filters_and_thins_to_one_per_second():
    buf = PoseBuffer()
    for i in range(0, 41):  # 0.0 .. 4.0 s at 10 Hz
        buf.add(_pose(i / 10))
    pts = buf.track(1.0)
    assert [round(p.ts, 1) for p in pts] == [1.0, 2.0, 3.0, 4.0]
    assert buf.track(99.0) == []


def test_thread_safety_smoke():
    buf = PoseBuffer(capacity=100)
    errors: list[BaseException] = []

    def writer(base: float) -> None:
        try:
            for i in range(500):
                buf.add(_pose(base + i * 0.01))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def reader() -> None:
        try:
            for i in range(500):
                buf.nearest(i * 0.01)
                buf.latest()
                buf.track(0.0)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(b,)) for b in (0.0, 100.0)]
    threads += [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)
    assert errors == []
    assert len(buf.track(-1.0)) <= 100


def test_no_telemetry():
    nt = NoTelemetry()
    frame = Frame(image=np.zeros((2, 2, 3), np.uint8), ts=0.0, wall_time=datetime.now(), index=0)
    nt.start()
    assert nt.state == "none"
    assert nt.pose_for(frame) is None
    assert nt.latest() is None
    assert nt.track(10.0) == []
    nt.stop()
    t: Telemetry = nt  # satisfies the protocol
    assert t.state == "none"
