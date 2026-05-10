"""Frame sources for the orchestrator.

Two kinds of input today:
  - WebcamSource(device_id) — live cv2.VideoCapture
  - DirSource(path)         — replays saved frames from a directory (testing)

`MultiCamera` combines named sources into a single read() that returns
a dict like {"wrist": ndarray, "env": ndarray}.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

import numpy as np


class FrameSource(Protocol):
    def read(self) -> np.ndarray: ...
    def close(self) -> None: ...


class WebcamSource:
    def __init__(self, device_id: int):
        import cv2  # imported lazily so dry-run/stub paths don't require opencv at import-time
        self._cv2 = cv2
        self.device_id = device_id
        self.cap = cv2.VideoCapture(device_id)
        if not self.cap.isOpened():
            raise RuntimeError(f"could not open camera {device_id}")

    def read(self) -> np.ndarray:
        ok, frame = self.cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"camera {self.device_id} returned no frame")
        return frame  # BGR

    def close(self) -> None:
        self.cap.release()


class DirSource:
    """Replays frames from a directory in lexicographic order, looping at the end."""

    def __init__(self, path: str | os.PathLike):
        import cv2
        self._cv2 = cv2
        self.dir = Path(path)
        self.frames = sorted(p for p in self.dir.iterdir()
                             if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
        if not self.frames:
            raise RuntimeError(f"no frames in {self.dir}")
        self._idx = 0

    def read(self) -> np.ndarray:
        path = self.frames[self._idx % len(self.frames)]
        self._idx += 1
        img = self._cv2.imread(str(path))
        if img is None:
            raise RuntimeError(f"could not read {path}")
        return img

    def close(self) -> None:
        pass


class MultiCamera:
    def __init__(self, sources: dict[str, FrameSource]):
        self.sources = sources

    def read(self) -> dict[str, np.ndarray]:
        return {name: src.read() for name, src in self.sources.items()}

    def close(self) -> None:
        for src in self.sources.values():
            src.close()


def build(cfg: dict) -> MultiCamera:
    sources: dict[str, FrameSource] = {}
    for name, value in cfg.items():
        if isinstance(value, int):
            sources[name] = WebcamSource(value)
        elif isinstance(value, str) and Path(value).is_dir():
            sources[name] = DirSource(value)
        elif isinstance(value, str) and value.isdigit():
            sources[name] = WebcamSource(int(value))
        else:
            raise ValueError(f"camera {name}: unsupported source spec {value!r}")
    return MultiCamera(sources)
