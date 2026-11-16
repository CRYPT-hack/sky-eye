import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from engine.core import Frame
from engine.telemetry.dji_srt import DjiSrtTelemetry, parse_srt

NEW = """1
00:00:00,000 --> 00:00:01,000
<font size="28">SrtCnt : 1, DiffTime : 1000ms
2024-05-01 10:00:00.000
[iso : 100] [latitude: 22.500000] [longitude: 113.900000] [rel_alt: 50.200 abs_alt: 120.000] [yaw: 30.0] [gb_yaw: 10.0 gb_pitch: -45.0 gb_roll: 2.0]</font>

2
00:00:01,000 --> 00:00:02,000
<font size="28">SrtCnt : 2
[latitude: -22.600000] [longitude: -113.800000] [rel_alt: 51.000 abs_alt: 121.000] [gb_yaw: 15.0 gb_pitch: -50.0 gb_roll: 0.0]</font>
"""

LEGACY = """1
00:00:00,000 --> 00:00:00,033
HOME(113.9, 22.5) 2024.05.01 10:00:00
GPS(113.901, 22.501, 18) BAROMETER:48.5
HS 3.2m/s H 50.2m D 10m

2
00:00:00,033 --> 00:00:00,066
GPS(113.902, -22.502, 18) BAROMETER:40.0
"""


# Mavic Pro style: "GPS (" with a space, distance D and height H in metres.
MAVIC_PRO = """1
00:00:00,000 --> 00:00:01,000
GPS (8.6234, 47.3828, 19), D 2.53m, H 6.80m, H.S 0.00m/s, V.S 0.00m/s

2
00:00:01,000 --> 00:00:02,000
GPS (8.6235, 47.3829, 19), D 2.60m, H 7.10m, H.S 0.50m/s, V.S 0.10m/s
"""

# Mavic Air 2 / Mini 2 firmware: "longtitude" (sic) and an "altitude" that may be absolute.
AIR2 = """1
00:00:00,000 --> 00:00:00,033
<font size="36">FrameCnt : 1, DiffTime : 33ms
2021-03-21 12:41:26,079,288
[iso : 100] [shutter : 1/1000.0] [fnum : 280] [ev : 0] [ct : 5500] [color_md : default] [focal_len : 240] [latitude: 47.382800] [longtitude: 8.623400] [altitude: 431.500000] </font>

2
00:00:00,033 --> 00:00:00,066
<font size="36">FrameCnt : 2, DiffTime : 33ms
2021-03-21 12:41:26,112,621
[iso : 100] [shutter : 1/1000.0] [fnum : 280] [ev : 0] [ct : 5500] [color_md : default] [focal_len : 240] [latitude: 47.382810] [longtitude: 8.623410] [altitude: 431.600000] </font>
"""


def _frame(ts: float) -> Frame:
    return Frame(np.zeros((2, 2, 3), np.uint8), ts, datetime(2024, 1, 1), 0)


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "flight.SRT"
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_new_format():
    poses = parse_srt(NEW)
    assert len(poses) == 2
    p = poses[0]
    assert (p.ts, p.lat, p.lon, p.alt_agl_m) == (0.0, 22.5, 113.9, 50.2)
    assert (p.yaw_deg, p.cam_pitch_deg, p.cam_roll_deg) == (10.0, -45.0, 2.0)  # gb_yaw wins
    q = poses[1]
    assert q.ts == 1.0
    assert (q.lat, q.lon, q.alt_agl_m) == (-22.6, -113.8, 51.0)
    assert q.yaw_deg == 15.0


def test_drone_yaw_used_without_gimbal_yaw():
    poses = parse_srt(NEW.replace("gb_yaw: 10.0 ", ""))
    assert (poses[0].yaw_deg, poses[0].yaw_assumed) == (30.0, False)


def test_parse_mavic_pro_gps_with_space():
    poses = parse_srt(MAVIC_PRO)
    assert len(poses) == 2
    p = poses[0]
    assert (p.lat, p.lon, p.alt_agl_m) == (47.3828, 8.6234, 6.80)  # H, not D or the 19
    assert (p.pitch_assumed, p.yaw_assumed, p.alt_assumed) == (True, True, False)
    assert poses[1].ts == 1.0 and poses[1].alt_agl_m == 7.10


def test_parse_air2_longtitude_and_altitude(caplog):
    with caplog.at_level(logging.INFO):
        poses = parse_srt(AIR2)
    assert len(poses) == 2
    p = poses[0]
    assert (p.lat, p.lon, p.alt_agl_m) == (47.3828, 8.6234, 431.5)
    assert p.alt_assumed and poses[1].alt_assumed
    notes = [r.getMessage() for r in caplog.records if "altitude" in r.getMessage()]
    assert len(notes) == 1 and "absolute" in notes[0]


def test_altitude_only_as_last_resort():
    measured = AIR2.replace("[altitude: 431.500000]", "[rel_alt: 40.000 abs_alt: 431.5] [altitude: 431.5]")
    p = parse_srt(measured)[0]
    assert (p.alt_agl_m, p.alt_assumed) == (40.0, False)
    with_h = MAVIC_PRO.replace("H 6.80m,", "H 6.80m, [altitude: 99.0],")
    p = parse_srt(with_h)[0]
    assert (p.alt_agl_m, p.alt_assumed) == (6.80, False)


def test_null_island_positions_skipped():
    # DJI writes 0, 0 before the GPS has a fix
    no_fix = NEW.replace("[latitude: 22.500000] [longitude: 113.900000]",
                         "[latitude: 0.000000] [longitude: 0.000000]")
    poses = parse_srt(no_fix)
    assert [p.ts for p in poses] == [1.0]
    legacy = parse_srt(LEGACY.replace("GPS(113.901, 22.501, 18)", "GPS(0.0, 0.0, 0)"))
    assert [p.ts for p in legacy] == [0.033]
    # only both zero means "no fix": the equator or the prime meridian alone is real
    on_equator = parse_srt(NEW.replace("[latitude: 22.500000]", "[latitude: 0.000000]"))
    assert on_equator[0].lat == 0.0


def test_parse_legacy_format():
    poses = parse_srt(LEGACY)
    assert len(poses) == 2
    p = poses[0]
    assert (p.lat, p.lon, p.alt_agl_m) == (22.501, 113.901, 50.2)  # H, not HS or GPS 3rd value
    assert p.cam_pitch_deg == -90.0
    q = poses[1]
    assert q.ts == 0.033
    assert (q.lat, q.lon, q.alt_agl_m) == (-22.502, 113.902, 40.0)  # BAROMETER fallback


def test_gimbal_missing_defaults_nadir(caplog):
    text = NEW.replace("gb_pitch: -45.0", "").replace("gb_pitch: -50.0", "")
    with caplog.at_level(logging.INFO):
        poses = parse_srt(text)
    assert [p.cam_pitch_deg for p in poses] == [-90.0, -90.0]
    assert [p.pitch_assumed for p in poses] == [True, True]
    assert sum("pitch" in r.getMessage() for r in caplog.records) == 1


def test_measured_angles_are_not_assumed():
    poses = parse_srt(NEW)
    assert [(p.pitch_assumed, p.yaw_assumed, p.alt_assumed) for p in poses] == [
        (False, False, False), (False, False, False)]


def test_configured_pitch_replaces_a_missing_gimbal_pitch(caplog):
    text = NEW.replace("gb_pitch: -45.0", "").replace("gb_pitch: -50.0", "")
    with caplog.at_level(logging.INFO):
        poses = parse_srt(text, pitch_deg=-60.0)
    assert [(p.cam_pitch_deg, p.pitch_assumed) for p in poses] == [(-60.0, False)] * 2
    assert "camera.pitch_deg" in caplog.text
    # a measured pitch still wins
    assert parse_srt(NEW, pitch_deg=-60.0)[0].cam_pitch_deg == -45.0


def test_missing_yaw_is_assumed_north():
    text = NEW.replace("[yaw: 30.0] ", "").replace("gb_yaw: 10.0 ", "").replace("gb_yaw: 15.0 ", "")
    poses = parse_srt(text)
    assert [(p.yaw_deg, p.yaw_assumed) for p in poses] == [(0.0, True), (0.0, True)]


def test_garbage_lines_skipped():
    text = "garbage\n\n" + "9\n00:00:00,000 --> 00:00:01,000\nno gps here\n\n" + NEW
    assert len(parse_srt(text)) == 2
    assert parse_srt("") == []


def _wait_loaded(t, timeout=5.0):
    deadline = time.monotonic() + timeout
    while t.state == "waiting" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert t.state != "waiting"


def test_start_returns_before_parsing_finishes(tmp_path, monkeypatch):
    import engine.telemetry.dji_srt as mod

    release = threading.Event()
    real = mod._parse

    def slow(text, *args):
        release.wait(5)
        return real(text, *args)

    monkeypatch.setattr(mod, "_parse", slow)
    t = DjiSrtTelemetry(_write(tmp_path, NEW), max_age_s=0.5)
    try:
        t.start()  # must not block on the parser
        assert t.state == "waiting"
        assert t.pose_for(_frame(0.2)) is None and t.latest() is None and t.track(5.0) == []
        release.set()
        _wait_loaded(t)
        assert t.state == "ok"
    finally:
        release.set()
        t.stop()


def test_pose_for_uses_video_time(tmp_path):
    t = DjiSrtTelemetry(_write(tmp_path, NEW), max_age_s=0.5)
    assert t.state == "waiting"
    t.start()
    _wait_loaded(t)
    assert t.state == "ok"
    assert t.latest() is None
    assert t.pose_for(_frame(0.2)).lat == 22.5
    assert t.pose_for(_frame(0.9)).lat == -22.6
    assert t.pose_for(_frame(5.0)) is None
    t.pose_for(_frame(1.0))
    assert t.latest().ts == 1.0
    assert [p.ts for p in t.track(5.0)] == [0.0, 1.0]


def test_pose_for_wraps_when_looping(tmp_path):
    path = _write(tmp_path, NEW)
    looping = DjiSrtTelemetry(path, max_age_s=0.5, loop=True)
    looping.start()
    _wait_loaded(looping)
    assert looping.pose_for(_frame(4.1)).ts == 0.0  # period 2.0 -> 0.1
    flat = DjiSrtTelemetry(path, max_age_s=0.5)
    flat.start()
    _wait_loaded(flat)
    assert flat.pose_for(_frame(4.1)) is None


def test_missing_file_is_stale(tmp_path, caplog):
    t = DjiSrtTelemetry(tmp_path / "nope.SRT", max_age_s=0.5)
    with caplog.at_level(logging.WARNING):
        t.start()
        _wait_loaded(t)
    assert t.state == "stale"
    assert t.pose_for(_frame(0.0)) is None
    assert t.latest() is None
    assert t.track(5.0) == []
    assert any("nope.SRT" in r.getMessage() for r in caplog.records)
    assert str(tmp_path) not in caplog.text and tmp_path.name not in caplog.text


def test_load_logs_positions_and_span_by_file_name(tmp_path, caplog):
    t = DjiSrtTelemetry(_write(tmp_path, NEW), max_age_s=0.5)
    with caplog.at_level(logging.INFO, logger="engine.telemetry.dji_srt"):
        t.load()
    assert "SRT flight.SRT: 2 positions over 2.0 s" in caplog.text
    assert str(tmp_path) not in caplog.text and tmp_path.name not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_unrecognised_format_warns(tmp_path, caplog):
    text = ("1\n00:00:00,000 --> 00:00:01,000\nISO 100 EV 0\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nISO 100 EV 0\n\n"
            "3\n00:00:02,000 --> 00:00:03,000\nISO 100 EV 0\n")
    t = DjiSrtTelemetry(_write(tmp_path, text), max_age_s=0.5)
    with caplog.at_level(logging.INFO, logger="engine.telemetry.dji_srt"):
        t.load()
    assert t.state == "stale"
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [
        "SRT flight.SRT: none of the 3 subtitle blocks had a position — format not recognised"]
    assert str(tmp_path) not in caplog.text


def test_state_is_stale_when_playback_runs_past_the_log(tmp_path):
    t = DjiSrtTelemetry(_write(tmp_path, NEW), max_age_s=0.5)
    t.load()
    assert t.state == "ok"
    assert t.pose_for(_frame(0.2)) is not None and t.state == "ok"
    assert t.pose_for(_frame(5.0)) is None
    assert t.state == "stale"
    assert t.pose_for(_frame(0.9)) is not None and t.state == "ok"


def test_load_is_synchronous_and_idempotent(tmp_path):
    t = DjiSrtTelemetry(_write(tmp_path, NEW), max_age_s=0.5)
    assert t.state == "waiting"
    t.load()
    assert t.state == "ok"
    assert t.pose_for(_frame(0.2)).lat == 22.5
    t.load()  # second call: no re-parse, same result
    assert t.state == "ok" and t.pose_for(_frame(0.9)).lat == -22.6
    t.start()  # already loaded: no helper thread
    assert t._loader is None
    assert t.state == "ok"


TEL_SRT = "telemetry:" + chr(10) + "  type: dji_srt" + chr(10)


def test_build_telemetry_loads_srt_before_start(tmp_path):
    from engine.config import load_config
    from engine.telemetry import build_telemetry
    from tests.conftest import make_video
    from tests.test_cli import write_config

    video = make_video(tmp_path / "v.mp4", 5)
    (tmp_path / "v.srt").write_text(NEW, encoding="utf-8")
    cfg = load_config(write_config(
        tmp_path, source=f"  path: {video.as_posix()}", extra=TEL_SRT))
    t = build_telemetry(cfg)
    assert t.state == "ok"  # poses are there before start() and before the first frame
    assert t.pose_for(_frame(0.2)).lat == 22.5


def test_build_telemetry_passes_camera_pitch(tmp_path):
    from engine.config import load_config
    from engine.telemetry import build_telemetry
    from tests.conftest import make_video
    from tests.test_cli import write_config

    video = make_video(tmp_path / "v.mp4", 5)
    (tmp_path / "v.srt").write_text(NEW.replace("gb_pitch: -45.0", ""), encoding="utf-8")
    cfg = load_config(write_config(
        tmp_path, source=f"  path: {video.as_posix()}",
        extra=TEL_SRT + "camera:" + chr(10) + "  pitch_deg: -70" + chr(10)))
    p = build_telemetry(cfg).pose_for(_frame(0.2))
    assert (p.cam_pitch_deg, p.pitch_assumed) == (-70.0, False)
