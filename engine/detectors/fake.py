from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

from engine.core import Detection


class FakeDetector:
    """Scripted detector for tests: detect() returns script(call_index)."""

    def __init__(self, script: Callable[[int], list[Detection]], confidence: float = 0.4):
        self._script = script
        self.confidence = confidence  # settable like RFDETRDetector; not used for filtering
        self._calls = 0

    def detect(self, image: np.ndarray) -> list[Detection]:
        out = self._script(self._calls)
        self._calls += 1
        return out

    def detect_raw(
        self, image: np.ndarray, floor: float, class_names: Sequence[str]
    ) -> list[Detection]:
        # goes through detect() so subclasses that override it keep working
        return self.detect(image)
