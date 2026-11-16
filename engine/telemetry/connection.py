"""Validation of the `telemetry.connection` string (UDP or serial only)."""
from __future__ import annotations

import re

_UDP = re.compile(r"(?:udp|udpin):[^:,\s]+:(\d{1,5})")
_SENDING = ("udpout", "udpbcast")
_SERIAL = re.compile(r"(?:COM\d+|/dev/[^,\s]+)(?:,\d+)?", re.IGNORECASE)


def validate_connection(connection: str) -> str:
    """Return `connection` if it is a listening UDP link (udp:/udpin:) or a serial port,
    else raise ValueError.

    The message carries only the scheme, never the full string (it may hold credentials).
    """
    s = connection.strip() if isinstance(connection, str) else ""
    m = _UDP.fullmatch(s)
    if m and int(m.group(1)) <= 65535:
        return connection
    if _SERIAL.fullmatch(s) and (s[:4].upper().startswith("COM") or s.startswith("/dev/")):
        return connection
    scheme = s.split(":", 1)[0] if ":" in s else "(no scheme)"
    if scheme in _SENDING:
        raise ValueError(
            f"telemetry.connection scheme {scheme!r} sends, but sky-eye only listens "
            "(the drone would never learn where to send); use a listening UDP link "
            "(e.g. udpin:0.0.0.0:14550) or a serial port"
        )
    raise ValueError(
        f"telemetry.connection has an unsupported or malformed link (scheme: {scheme!r}); "
        "use UDP (e.g. udpin:0.0.0.0:14550) or a serial port (e.g. COM5,57600 or /dev/ttyUSB0)"
    )
