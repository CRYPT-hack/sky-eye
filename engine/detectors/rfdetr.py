from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from engine.config import DetectorConfig
from engine.core import Detection

log = logging.getLogger(__name__)

SIZES = ("nano", "small", "medium")


class DetectorError(Exception):
    pass


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
                raise DetectorError(
                    f"could not build RF-DETR {size} (check detector.input_size={input_size}): {exc}"
                ) from exc
            optimize = getattr(model, "optimize_for_inference", None)
            if optimize is not None:
                try:
                    optimize()
                except Exception as exc:  # optimization is best-effort
                    log.warning("optimize_for_inference failed: %s", exc)
        self._model = model

    def detect(self, image_bgr: np.ndarray) -> list[Detection]:
        rgb = np.ascontiguousarray(image_bgr[..., ::-1])
        dets = self._model.predict(rgb, threshold=self.confidence, include_source_image=False)
        out: list[Detection] = []
        if dets.class_id is None or dets.confidence is None:
            return out
        for box, score, cid in zip(dets.xyxy, dets.confidence, dets.class_id):
            name = self._id_to_name.get(int(cid))
            if name is None:
                continue
            out.append(Detection(tuple(float(v) for v in box), float(score), name))
        return out


def build_detector(cfg: DetectorConfig) -> RFDETRDetector:
    return RFDETRDetector(
        size=cfg.size,
        weights=cfg.weights,
        input_size=cfg.input_size,
        confidence=cfg.confidence,
        device=cfg.device,
        classes=cfg.classes,
    )
