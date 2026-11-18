from __future__ import annotations

import numpy as np
import pytest
import supervision as sv

from engine.core import Detection
from engine.detectors import DetectorError, RFDETRDetector
from engine.detectors.filtering import filter_detections


def D(score, name="truck"):
    return Detection((0.0, 0.0, 10.0, 10.0), score, name)


def test_filter_keeps_classes_and_threshold():
    raw = [D(0.5), D(0.4), D(0.39), D(0.9, "car")]
    out = filter_detections(raw, ["truck"], 0.4)
    assert [(d.class_name, d.score) for d in out] == [("truck", 0.5), ("truck", 0.4)]


def test_filter_keeps_order():
    raw = [D(0.6, "bus"), D(0.9), D(0.7, "bus"), D(0.5)]
    out = filter_detections(raw, ["truck", "bus"], 0.5)
    assert [d.score for d in out] == [0.6, 0.9, 0.7, 0.5]


class StubModel:
    def __init__(self):
        self.calls = []

    def predict(self, images, threshold=0.5, **kwargs):
        self.calls.append(threshold)
        return sv.Detections(
            xyxy=np.array([[0, 0, 10, 10], [20, 20, 60, 60], [5, 5, 50, 50]], dtype=float),
            confidence=np.array([0.9, 0.3, 0.7]),
            class_id=np.array([3, 8, 6]),  # car, truck, bus
        )


def make(model):
    return RFDETRDetector(size="small", weights=None, input_size=512, confidence=0.5,
                          device="cpu", classes=["truck"], model=model)


IMG = np.zeros((8, 8, 3), np.uint8)


def test_detect_raw_returns_named_classes_above_floor():
    m = StubModel()
    out = make(m).detect_raw(IMG, 0.2, ["truck", "car", "bus"])
    assert m.calls == [0.2]
    assert [d.class_name for d in out] == ["car", "truck", "bus"]


def test_detect_raw_unknown_class_raises():
    with pytest.raises(DetectorError):
        make(StubModel()).detect_raw(IMG, 0.2, ["spaceship"])


def test_detect_uses_confidence_and_configured_classes():
    m = StubModel()
    out = make(m).detect(IMG)
    assert m.calls == [0.5]
    assert out == []  # the truck scored 0.3 < 0.5 anyway; car and bus are not configured


def test_merge_class_duplicates_overlap():
    from engine.detectors.filtering import merge_class_duplicates

    a = Detection((0.0, 0.0, 10.0, 10.0), 0.3, "truck")
    b = Detection((0.0, 0.0, 10.0, 9.0), 0.5, "car")
    out = merge_class_duplicates([a, b])
    assert len(out) == 1
    assert out[0][0] is b
    assert out[0][1] == {"car": 0.5, "truck": 0.3}


def test_merge_class_duplicates_separate_and_ordered():
    from engine.detectors.filtering import merge_class_duplicates

    a = Detection((0.0, 0.0, 10.0, 10.0), 0.3, "truck")
    b = Detection((50.0, 50.0, 60.0, 60.0), 0.6, "car")
    out = merge_class_duplicates([a, b])
    assert [o[0] for o in out] == [b, a]


def test_merge_class_duplicates_tie_uses_input_order():
    from engine.detectors.filtering import merge_class_duplicates

    a = Detection((0.0, 0.0, 10.0, 10.0), 0.4, "truck")
    b = Detection((0.0, 0.0, 10.0, 10.0), 0.4, "car")
    out = merge_class_duplicates([a, b])
    assert len(out) == 1 and out[0][0] is a
    assert out[0][1] == {"truck": 0.4, "car": 0.4}
    assert merge_class_duplicates([b, a])[0][0] is b
