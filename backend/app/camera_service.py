from __future__ import annotations

import threading
import time


class CameraService:
    """
    Holds the latest camera frame pushed from the browser.

    We keep it intentionally simple: one latest JPEG/PNG buffer + timestamp.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame_bytes: bytes | None = None
        self._frame_ts: float = 0.0
        self._content_type: str = "image/jpeg"

    def set_frame(self, frame_bytes: bytes, *, content_type: str | None = None) -> None:
        if not frame_bytes:
            return
        ct = (content_type or "").strip().lower() or "image/jpeg"
        with self._lock:
            self._frame_bytes = bytes(frame_bytes)
            self._frame_ts = time.time()
            self._content_type = ct

    def get_frame(self) -> tuple[bytes | None, float, str]:
        with self._lock:
            return self._frame_bytes, float(self._frame_ts), str(self._content_type)

