from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml


class PerfService:
    """
    Stores learned performance tuning keyed by matrix size + pattern.

    Persisted to config/perf.yaml. This is non-sensitive local data.
    """

    def __init__(self, perf_path: Path) -> None:
        self._lock = threading.RLock()
        self._perf_path = perf_path
        self._data: Dict[str, Any] = self._load()
        self._save_timer: threading.Timer | None = None

    def _load(self) -> Dict[str, Any]:
        if not self._perf_path.exists():
            return {"profiles": {}}
        try:
            obj = yaml.safe_load(self._perf_path.read_text(encoding="utf-8")) or {}
        except Exception:
            obj = {}
        if "profiles" not in obj or not isinstance(obj["profiles"], dict):
            obj["profiles"] = {}
        return obj

    def _schedule_save(self) -> None:
        if self._save_timer:
            try:
                self._save_timer.cancel()
            except Exception:
                pass
        self._save_timer = threading.Timer(0.5, self._save_now)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _save_now(self) -> None:
        with self._lock:
            self._perf_path.parent.mkdir(parents=True, exist_ok=True)
            self._perf_path.write_text(
                yaml.safe_dump(self._data, sort_keys=False),
                encoding="utf-8",
            )

    @staticmethod
    def key_for(width: int, height: int, pattern: str) -> str:
        return f"{width}x{height}/{pattern}"

    def get_learned_max_fps(self, width: int, height: int, pattern: str) -> Optional[int]:
        key = self.key_for(width, height, pattern)
        with self._lock:
            prof = (self._data.get("profiles") or {}).get(key) or {}
            v = prof.get("learned_max_fps")
            if isinstance(v, int) and 1 <= v <= 120:
                return v
            return None

    def set_learned_max_fps(self, width: int, height: int, pattern: str, learned_max_fps: int) -> None:
        key = self.key_for(width, height, pattern)
        learned_max_fps = int(max(1, min(120, learned_max_fps)))
        with self._lock:
            profiles = self._data.setdefault("profiles", {})
            prof = profiles.setdefault(key, {})
            prof["learned_max_fps"] = learned_max_fps
            prof["updated_at"] = int(time.time())
            self._schedule_save()

    def reset_profile(self, width: int, height: int, pattern: str) -> None:
        key = self.key_for(width, height, pattern)
        with self._lock:
            profiles = self._data.setdefault("profiles", {})
            if key in profiles:
                del profiles[key]
                self._schedule_save()

    def dump(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

