"""Geolocation maths, checked against expectations worked out by hand.

Frames: ENU (x east, y north, z up), drone at (0, 0, alt). Image x right, y down,
principal point at the frame centre, f = (w/2) / tan(hfov/2) px.
"""
import math

import pytest

from engine.geo import GeoFix, geolocate
from engine.telemetry import DronePose

# 1000x1000 frame, hfov 90 deg -> f = 500 / tan(45 deg) = 500 px, centre (500, 500).
SQUARE = (1000, 1000)
HFOV = 90.0
TAN2 = 0.03492076949174773  # tan(2 deg)

# WGS-84, written out here independently of engine.geo.
A = 6378137.0
E2 = 0.00669437999014


def _pose(yaw=0.0, pitch=-90.0, roll=0.0, alt=100.0) -> DronePose:
    return DronePose(ts=0.0, lat=45.0, lon=10.0, alt_agl_m=alt, yaw_deg=yaw,
                     cam_pitch_deg=pitch, cam_roll_deg=roll)


def _offset_m(pose: DronePose, fix: GeoFix) -> tuple[float, float]:
    """(east, north) metres from the drone to the fix, via the WGS-84 radii of curvature."""
    phi = math.radians(pose.lat)
    s2 = 1 - E2 * math.sin(phi) ** 2
    r_m = A * (1 - E2) / s2 ** 1.5  # meridian radius
    r_n = A / math.sqrt(s2)  # prime-vertical radius
    north = math.radians(fix.lat - pose.lat) * r_m
    east = math.radians(fix.lon - pose.lon) * r_n * math.cos(phi)
    return east, north


def _assert_offset(pose, fix, east, north):
    got_e, got_n = _offset_m(pose, fix)
    assert got_e == pytest.approx(east, abs=0.01)
    assert got_n == pytest.approx(north, abs=0.01)


def test_nadir_centre_is_below_drone():
    # Tall box: its middle (y = 400) is off-centre but its bottom-centre (500, 500) is
    # the principal point, so the ray goes straight down.
    pose = _pose(yaw=37.0)
    fix = geolocate((450.0, 300.0, 550.0, 500.0), SQUARE, pose, HFOV)
    assert fix.status == "ok"
    assert fix.lat == pytest.approx(45.0, abs=1e-12)
    assert fix.lon == pytest.approx(10.0, abs=1e-12)
    # slant 100 m, depression 90 deg: 3 + 100 * tan(2) / sin(90) = 3 + 3.492077 = 6.492077
    assert fix.error_m == pytest.approx(6.492077, abs=1e-6)


def test_nadir_offset_pixel():
    # Bottom-centre (600, 450): 100 px right, 50 px above centre. At nadir, yaw 0,
    # image-right = east and image-top = north, so 100 m * (100/500, 50/500) = 20 m E, 10 m N.
    pose = _pose()
    fix = geolocate((560.0, 400.0, 640.0, 450.0), SQUARE, pose, HFOV)
    assert fix.status == "ok"
    _assert_offset(pose, fix, east=20.0, north=10.0)
    # Pin the degrees too: at 45 deg, 1 - e2 * sin^2 = 1 - 0.00669438 / 2 = 0.99665281,
    # R_M = 6378137 * 0.99330562 / 0.99665281^1.5 = 6367381.8156 m,
    # R_N * cos = 6378137 / sqrt(0.99665281) * cos(45) = 4517590.8788 m.
    assert fix.lat - 45.0 == pytest.approx(math.degrees(10.0 / 6367381.8156), abs=1e-9)
    assert fix.lon - 10.0 == pytest.approx(math.degrees(20.0 / 4517590.8788), abs=1e-9)


def test_yaw_rotates_offset():
    # Same pixel, yaw 90: at nadir image-top points east and image-right points south,
    # so 20 m "right" -> 20 m south and 10 m "up" -> 10 m east.
    pose = _pose(yaw=90.0)
    fix = geolocate((560.0, 400.0, 640.0, 450.0), SQUARE, pose, HFOV)
    assert fix.status == "ok"
    _assert_offset(pose, fix, east=10.0, north=-20.0)


def test_roll_at_nadir():
    # Positive roll = clockwise about the boresight seen from behind; at nadir that is
    # seen from above with north up, so image-right swings from east to south. A pixel
    # 100 px right of centre (bottom-centre (600, 500)) -> 100 * 100/500 = 20 m SOUTH.
    pose = _pose(roll=90.0)
    fix = geolocate((560.0, 450.0, 640.0, 500.0), SQUARE, pose, HFOV)
    assert fix.status == "ok"
    _assert_offset(pose, fix, east=0.0, north=-20.0)


def test_frame_aspect_is_honoured():
    # 1000x500 frame, hfov 90: f = 500 px from the width; centre (500, 250).
    pose = _pose()
    # Bottom-centre (500, 50): 200 px above centre -> 100 * 200/500 = 40 m north.
    # (f from the height would give 80 m; a centre at (500, 500) would give 90 m.)
    fix = geolocate((460.0, 10.0, 540.0, 50.0), (1000, 500), pose, HFOV)
    assert fix.status == "ok"
    _assert_offset(pose, fix, east=0.0, north=40.0)
    # Bottom-centre (900, 250): 400 px right of centre -> 100 * 400/500 = 80 m east.
    fix = geolocate((860.0, 200.0, 940.0, 250.0), (1000, 500), pose, HFOV)
    _assert_offset(pose, fix, east=80.0, north=0.0)


def test_45deg_pitch_forward_distance():
    # Centre pixel, pitch -45, yaw 0: ground point alt / tan(45) = 100 m north.
    pose = _pose(pitch=-45.0)
    fix = geolocate((450.0, 400.0, 550.0, 500.0), SQUARE, pose, HFOV)
    assert fix.status == "ok"
    _assert_offset(pose, fix, east=0.0, north=100.0)
    # slant = 100 * sqrt(2); slant / sin(45) = 200 -> 3 + 200 * tan(2) = 3 + 6.984154 = 9.984154
    assert fix.error_m == pytest.approx(3.0 + 200.0 * TAN2, abs=1e-6)


def test_near_horizon_unreliable():
    no_point = GeoFix(None, None, None, "unreliable")
    centre = (450.0, 400.0, 550.0, 500.0)
    # Horizontal camera, centre ray: d_z = 0, never meets the ground.
    assert geolocate(centre, SQUARE, _pose(pitch=0.0), HFOV) == no_point
    # Horizontal camera, pixel 100 px above centre: ray points up.
    assert geolocate((450.0, 300.0, 550.0, 400.0), SQUARE, _pose(pitch=0.0), HFOV) == no_point
    # Pitch -4: depression 4 deg <= 5 deg.
    assert geolocate(centre, SQUARE, _pose(pitch=-4.0), HFOV) == no_point


def test_ground_level_altitude_unreliable():
    centre = (450.0, 400.0, 550.0, 500.0)
    assert geolocate(centre, SQUARE, _pose(alt=0.0), HFOV) == GeoFix(None, None, None, "unreliable")


def test_error_over_200m_unreliable():
    centre = (450.0, 400.0, 550.0, 500.0)
    # Centre ray at depression d: slant = alt / sin(d), so error = 3 + alt * tan(2) / sin(d)^2.
    # Pitch -7: sin(7) = 0.121869, sin^2 = 0.014852 -> 3 + 3.492077 / 0.014852 = 238.12 > 200.
    pose = _pose(pitch=-7.0)
    fix = geolocate(centre, SQUARE, pose, HFOV)
    assert fix.status == "unreliable"
    assert fix.error_m == pytest.approx(238.12286, abs=1e-4)
    # Point kept for the error circle: 100 / tan(7) = 814.4346 m north.
    _assert_offset(pose, fix, east=0.0, north=814.4346)
    # Pitch -8: sin^2(8) = 0.019369 -> 3 + 3.492077 / 0.019369 = 183.29 <= 200 -> ok.
    fix = geolocate(centre, SQUARE, _pose(pitch=-8.0), HFOV)
    assert fix.status == "ok"
    assert fix.error_m == pytest.approx(183.29065, abs=1e-4)


def test_no_pose_unknown():
    fix = geolocate((450.0, 400.0, 550.0, 500.0), SQUARE, None, HFOV)
    assert fix == GeoFix(None, None, None, "unknown")
