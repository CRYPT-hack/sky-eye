from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
import supervision as sv

from engine.config import DetectorConfig
from engine.core import Detection
from engine.detectors import DetectorError, FakeDetector, RFDETRDetector, build_detector

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
TRUCK_JPG = Path(__file__).resolve().parent / "data" / "truck.jpg"


class StubModel:
    def __init__(self):
        self.calls: list[dict] = []

    def predict(self, images, threshold=0.5, **kwargs):
        self.calls.append({"image": images, "threshold": threshold, **kwargs})
        return sv.Detections(
            xyxy=np.array([[0, 0, 10, 10], [20, 20, 60, 60], [5, 5, 50, 50]], dtype=float),
            confidence=np.array([0.9, 0.8, 0.7]),
            class_id=np.array([3, 8, 6]),  # car, truck, bus
        )


@pytest.fixture
def stub_model():
    return StubModel()


def make_detector(model, classes=("truck",), **kw):
    return RFDETRDetector(
        size="small", weights=None, input_size=512, confidence=0.4,
        device="cpu", classes=list(classes), model=model, **kw,
    )


def test_filters_to_requested_classes(stub_model):
    det = make_detector(stub_model, classes=["truck"])
    out = det.detect(np.zeros((240, 320, 3), np.uint8))
    assert [d.class_name for d in out] == ["truck"]
    assert out[0].score == pytest.approx(0.8)
    assert out[0].box == (20.0, 20.0, 60.0, 60.0)


def test_passes_rgb_to_model(stub_model):
    img = np.zeros((4, 4, 3), np.uint8)
    img[..., 0] = 255  # BGR blue
    make_detector(stub_model).detect(img)
    sent = stub_model.calls[0]["image"]
    assert tuple(sent[0, 0]) == (0, 0, 255)
    assert sent.flags["C_CONTIGUOUS"]
    assert stub_model.calls[0]["include_source_image"] is False


def test_uses_runtime_confidence(stub_model):
    det = make_detector(stub_model)
    det.confidence = 0.85
    det.detect(np.zeros((8, 8, 3), np.uint8))
    assert stub_model.calls[0]["threshold"] == 0.85


def test_unknown_class_rejected(stub_model):
    with pytest.raises(DetectorError, match="spaceship"):
        make_detector(stub_model, classes=["spaceship"])


def test_cuda_missing_error(monkeypatch):
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(DetectorError, match="device: cpu"):
        build_detector(DetectorConfig(device="cuda"))


def test_missing_weights_error(tmp_path):
    missing = tmp_path / "nope.pth"
    with pytest.raises(DetectorError, match="nope.pth"):
        build_detector(DetectorConfig(weights=missing, device="cpu"))


def test_fake_detector_scripted_calls():
    d = Detection((0, 0, 1, 1), 0.9, "truck")
    fake = FakeDetector(lambda i: [d] if i == 1 else [])
    img = np.zeros((2, 2, 3), np.uint8)
    assert fake.detect(img) == []
    assert fake.detect(img) == [d]
    fake.confidence = 0.7
    assert fake.confidence == 0.7


@pytest.mark.gpu
def test_real_model_runs_on_cuda(capsys):
    video = SAMPLES / "drone.mp4"
    if not video.exists() and not TRUCK_JPG.exists():
        pytest.skip("no sample video or truck.jpg")
    detector = build_detector(DetectorConfig())
    if video.exists():
        cap = cv2.VideoCapture(str(video))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * 0.2))
        ok, frame = cap.read()
        cap.release()
        assert ok
        import time

        detector.detect(frame)  # warmup
        t0 = time.perf_counter()
        out = detector.detect(frame)
        ms = (time.perf_counter() - t0) * 1000
        assert isinstance(out, list)
        assert all(isinstance(d, Detection) and d.class_name == "truck" for d in out)
        with capsys.disabled():
            print(f"\nSAMPLE_FRAME trucks={len(out)} infer_ms={ms:.1f}")
    if TRUCK_JPG.exists():
        img = cv2.imread(str(TRUCK_JPG))
        assert any(d.class_name == "truck" for d in detector.detect(img))
