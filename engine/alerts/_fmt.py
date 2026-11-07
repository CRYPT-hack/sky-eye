"""Shared message formatting for notifiers."""

from __future__ import annotations

from datetime import datetime


def local_hms(iso: object) -> str:
    """ISO-8601 (...Z) -> HH:MM:SS in the engine machine's local zone; '?' if unparseable."""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone()
    except ValueError:
        return "?"
    return dt.strftime("%H:%M:%S")


def score_pct(truck: dict) -> str:
    try:
        return f"{float(truck.get('best_score', 0)) * 100:.0f}%"
    except (TypeError, ValueError):
        return "?"
