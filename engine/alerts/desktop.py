"""Desktop notifier: plyer toast plus an audible beep."""

from __future__ import annotations

import sys
from typing import Callable

from engine.alerts._fmt import local_hms, score_pct
from engine.alerts.core import AlertBatch


def _default_beep() -> None:
    if sys.platform == "win32":
        try:
            import winsound

            winsound.MessageBeep()
        except Exception:  # a missing sound device must not fail the alert
            pass
    else:
        print("\a", end="", flush=True)


def _default_notify(**kwargs) -> None:
    from plyer import notification

    notification.notify(**kwargs)


class DesktopNotifier:
    name = "desktop"

    def __init__(
        self,
        notify: Callable[..., None] | None = None,
        beep: Callable[[], None] | None = None,
    ) -> None:
        self._notify = notify or _default_notify
        self._beep = beep or _default_beep

    def send(self, batch: AlertBatch) -> None:
        if batch.is_test:
            message = "Test notification"
        elif len(batch.trucks) == 1:
            t = batch.trucks[0]
            message = f"Seen at {local_hms(t.get('last_seen'))}, score {score_pct(t)}"
        else:
            message = f"Latest at {local_hms(batch.trucks[-1].get('last_seen'))}"
        self._notify(title=batch.title(), message=message, app_name="sky-eye", timeout=5)
        self._beep()
