from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from engine.config import DetectorConfig
from engine.core import Detection
from engine.detectors.filtering import filter_detections

log = logging.getLogger(__name__)

SIZES = ("nano", "small", "medium")


class DetectorError(Exception):
    pass


def _weights_cache_dir() -> str:
    try:
        from rfdetr.assets.model_weights import get_model_cache_dir

        return get_model_cache_dir()
    except Exception:  # noqa: BLE001 - only used in an error message
        return "~/.roboflow/models"


def _build_error(size: str, input_size: int, weights: Path | None, exc: Exception) -> str:
    text = str(exc)
    if "resolution" in text.lower() or "divisible" in text.lower():
        return f"could not build RF-DETR {size} (check detector.input_size={input_size}): {text}"
    if weights is not None:
        return f"could not load the RF-DETR {size} weights from detector.weights {weights}: {text}"
    return (
        f"could not load or download the RF-DETR {size} model weights: {text}. "
        "The first run downloads them (internet access needed) into "
        f"{_weights_cache_dir()}; a partial download there can be deleted to retry."
    )


def _model_class(size: str):
    import rfdetr

    classes = {
        "nano": rfdetr.RFDETRNano,
        "small": rfdetr.RFDETRSmall,
        "medium": rfdetr.RFDETRMedium,
    }
    if size not in classes:
        raise DetectorError(f"unknown detector.size {size!r}; expected one of {list(classes)}")
    return classes[size]


class RFDETRDetector:
    def __init__(
        self,
        size: str,
        weights: Path | None,
        input_size: int,
        confidence: float,
        device: str,
        classes: list[str],
        model: Any | None = None,
    ):
        from rfdetr.assets.coco_classes import COCO_CLASSES

        name_to_id = {name: int(cid) for cid, name in COCO_CLASSES.items()}
        unknown = [c for c in classes if c not in name_to_id]
        if unknown:
            raise DetectorError(f"unknown detector.classes {unknown}; must be COCO class names")
        self._id_to_name = {name_to_id[c]: c for c in classes}
        self.confidence = confidence
        self._class_maps: dict[tuple[str, ...], dict[int, str]] = {}

        if model is None:
            if weights is not None and not Path(weights).is_file():
                raise DetectorError(f"detector.weights not found: {weights}")
            if device == "cuda":
                import torch

                if not torch.cuda.is_available():
                    raise DetectorError("CUDA is not available; set `device: cpu` in the config")
            kwargs: dict[str, Any] = {"resolution": input_size, "device": device}
            if weights is not None:
                kwargs["pretrain_weights"] = str(weights)
            try:
                model = _model_class(size)(**kwargs)
            except DetectorError:
                raise
            except Exception as exc:
                raise DetectorError(_build_error(size, input_size, weights, exc)) from exc
            optimize = getattr(model, "optimize_for_inference", None)
            if optimize is not None:
                try:
                    optimize()
                except Exception as exc:  # optimization is best-effort
                    log.warning("optimize_for_inference failed: %s", exc)
        self._model = model

    def _class_map(self, class_names: tuple[str, ...]) -> dict[int, str]:
        """COCO id -> name for the given classes; built once per tuple of names."""
        cached = self._class_maps.get(class_names)
        if cached is not None:
            return cached
        from rfdetr.assets.coco_classes import COCO_CLASSES

        name_to_id = {name: int(cid) for cid, name in COCO_CLASSES.items()}
        unknown = [c for c in class_names if c not in name_to_id]
        if unknown:
            raise DetectorError(f"unknown class names {unknown}; must be COCO class names")
        mapping = {name_to_id[c]: c for c in class_names}
        self._class_maps[class_names] = mapping
        return mapping

    def detect_raw(
        self, image_bgr: np.ndarray, floor: float, class_names: Sequence[str]
    ) -> list[Detection]:
        """Every detection of the named COCO classes with score >= floor."""
        id_to_name = self._class_map(tuple(class_names))

        rgb = np.ascontiguousarray(image_bgr[..., ::-1])
        dets = self._model.predict(rgb, threshold=floor, include_source_image=False)
        out: list[Detection] = []
        if dets.class_id is None or dets.confidence is None:
            return out
        for box, score, cid in zip(dets.xyxy, dets.confidence, dets.class_id):
            name = id_to_name.get(int(cid))
            if name is None:
                continue
            out.append(Detection(tuple(float(v) for v in box), float(score), name))
        return out

    def detect(self, image_bgr: np.ndarray) -> list[Detection]:
        classes = list(self._id_to_name.values())
        raw = self.detect_raw(image_bgr, self.confidence, classes)
        return filter_detections(raw, classes, self.confidence)


def build_detector(cfg: DetectorConfig) -> RFDETRDetector:
    return RFDETRDetector(
        size=cfg.size,
        weights=cfg.weights,
        input_size=cfg.input_size,
        confidence=cfg.confidence,
        device=cfg.device,
        classes=cfg.classes,
    )
