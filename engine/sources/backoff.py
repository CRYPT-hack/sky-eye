from __future__ import annotations


class Backoff:
    """Reconnect delays that double from `initial` up to `maximum`: 1, 2, 4, ... 30, 30."""

    def __init__(self, initial: float = 1.0, maximum: float = 30.0) -> None:
        if initial <= 0 or maximum < initial:
            raise ValueError("need 0 < initial <= maximum")
        self._initial = float(initial)
        self._maximum = float(maximum)
        self._next = self._initial

    def next_delay(self) -> float:
        delay = self._next
        self._next = min(self._next * 2, self._maximum)
        return delay

    def reset(self) -> None:
        self._next = self._initial
