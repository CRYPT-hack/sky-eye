"""Flat-ground pinhole geolocation: a detection box plus the drone pose -> ground lat/lon.

Frames: local ENU (x east, y north, z up) with the drone at (0, 0, alt_agl_m); image x
right, y down, principal point at the frame centre, square pixels. With yaw, pitch and
roll all 0 the camera looks horizontally north (image-right = east, image-up = up).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from engine.telemetry import DronePose

_WGS84_A = 6378137.0
_WGS84_E2 = 0.00669437999014
_MIN_DEPRESSION_DEG = 5.0
_MAX_ERROR_M = 200.0


@dataclass(frozen=True)
class GeoFix:
    lat: float | None
    lon: float | None
    error_m: float | None
    status: str  # "ok" | "unreliable" | "unknown"


def geolocate(
    box: tuple[float, float, float, float],
    frame_size: tuple[int, int],
    pose: DronePose | None,
    hfov_deg: float,
) -> GeoFix:
    """Where the ray through the box's bottom-centre pixel meets flat ground.

    A pose whose camera pitch or height was assumed (not measured) gives "unreliable"
    with an error of at least 200 m; an assumed heading (with a known pitch) widens the
    error by the distance the point could swing round the drone.
    """
    if pose is None:
        return GeoFix(None, None, None, "unknown")
    east, north, up = _ray_enu(box, frame_size, pose, hfov_deg)
    if up >= 0 or pose.alt_agl_m <= 0:
        return GeoFix(None, None, None, "unreliable")
    norm = math.sqrt(east * east + north * north + up * up)
    depression = math.asin(-up / norm)
    if math.degrees(depression) <= _MIN_DEPRESSION_DEG:
        return GeoFix(None, None, None, "unreliable")
    t = pose.alt_agl_m / -up
    slant_range = t * norm
    error_m = 3.0 + slant_range * math.tan(math.radians(2.0)) / max(math.sin(depression), 0.05)
    east_m, north_m = t * east, t * north
    if pose.yaw_assumed and not pose.pitch_assumed:
        # unknown heading: the point may be anywhere on the circle through it around the
        # drone's ground point, at most twice its distance from where it is drawn
        error_m += 2.0 * math.hypot(east_m, north_m)
    lat, lon = _offset_latlon(pose.lat, pose.lon, east_m, north_m)
    if pose.pitch_assumed or pose.alt_assumed:
        return GeoFix(lat, lon, max(error_m, _MAX_ERROR_M), "unreliable")
    return GeoFix(lat, lon, error_m, "unreliable" if error_m > _MAX_ERROR_M else "ok")


def _ray_enu(box, frame_size, pose: DronePose, hfov_deg: float) -> tuple[float, float, float]:
    """ENU direction (not normalised) of the ray through the box's bottom-centre pixel."""
    x1, _, x2, y2 = box
    w, h = frame_size
    f = (w / 2) / math.tan(math.radians(hfov_deg) / 2)
    # Camera frame (right, down, boresight), boresight component 1.
    right = ((x1 + x2) / 2 - w / 2) / f
    down = (y2 - h / 2) / f
    # Roll about the boresight, clockwise seen from behind: image-right swings toward down.
    r = math.radians(pose.cam_roll_deg)
    right, down = right * math.cos(r) - down * math.sin(r), right * math.sin(r) + down * math.cos(r)
    # Pitch about the right axis (-90 = boresight straight down) -> (right, forward, up).
    p = math.radians(pose.cam_pitch_deg)
    forward = down * math.sin(p) + math.cos(p)
    up = math.sin(p) - down * math.cos(p)
    # Yaw clockwise from north about up: forward = (sin y, cos y), right = (cos y, -sin y).
    y = math.radians(pose.yaw_deg)
    east = right * math.cos(y) + forward * math.sin(y)
    north = forward * math.cos(y) - right * math.sin(y)
    return east, north, up


def _offset_latlon(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    """Shift (lat, lon) by a local tangent-plane offset using the WGS-84 radii of curvature."""
    phi = math.radians(lat)
    s2 = 1 - _WGS84_E2 * math.sin(phi) ** 2
    r_n = _WGS84_A / math.sqrt(s2)  # prime vertical
    r_m = _WGS84_A * (1 - _WGS84_E2) / s2 ** 1.5  # meridian
    return lat + math.degrees(north_m / r_m), lon + math.degrees(east_m / (r_n * math.cos(phi)))
