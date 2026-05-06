from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict

import yaml

from app.models.settings import RuntimeSettings


class _SettingsDumper(yaml.SafeDumper):
    """Quote tricky strings so secrets / hex colors with '#' survive round-trips."""


def _represent_str(dumper: yaml.Dumper, value: str) -> yaml.nodes.ScalarNode:
    must_quote = (
        len(value) > 96
        or "\n" in value
        or "#" in value
        or ":" in value
        or "'" in value
        or '"' in value
        or value.startswith((" ", "!", "&", "*", "?", "[", "{", "@", "`"))
        or value.strip() != value
    )
    if must_quote:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_SettingsDumper.add_representer(str, _represent_str)


def sanitize_settings_patch(patch: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strip UI-only / runtime-only keys that must never be merged into persisted RuntimeSettings.

    The browser may briefly carry GET-response extras like api_key_configured or websocket-added learned_*.
    """

    blocked_root = frozenset({"learned", "effective_fps", "version"})
    out: Dict[str, Any] = {}
    for k, v in patch.items():
        if k in blocked_root:
            continue
        if k == "integrations" and isinstance(v, dict):
            out[k] = _sanitize_integrations_patch(v)
        else:
            out[k] = v
    return out


def _sanitize_integrations_patch(patch: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for ik, iv in patch.items():
        if ik == "openai" and isinstance(iv, dict):
            out[ik] = { kk: vv for kk, vv in iv.items() if kk != "api_key_configured" }
        else:
            out[ik] = iv
    return out


class SettingsService:
    """
    In-memory runtime settings with a small lock.

    - Loads defaults from config/settings.yaml (non-sensitive).
    - Allows partial updates from the UI at runtime without restart.
    """

    def __init__(self, settings_path: Path) -> None:
        self._lock = threading.RLock()
        self._settings_path = settings_path
        self._settings = self._load_defaults()
        self._bump_version()
        self._save_timer: threading.Timer | None = None

    def _load_defaults(self) -> RuntimeSettings:
        if not self._settings_path.exists():
            return RuntimeSettings()
        data = yaml.safe_load(self._settings_path.read_text(encoding="utf-8")) or {}
        return RuntimeSettings.model_validate(data)

    def _schedule_save(self) -> None:
        # Debounce disk writes so sliders don't thrash the filesystem.
        if self._save_timer:
            try:
                self._save_timer.cancel()
            except Exception:
                pass
        self._save_timer = threading.Timer(0.35, self._save_now)
        self._save_timer.daemon = True
        self._save_timer.start()

    def _save_now(self) -> None:
        with self._lock:
            data = self._persistable_dict()
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(
                yaml.dump(data, Dumper=_SettingsDumper, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )

    def _persistable_dict(self) -> Dict[str, Any]:
        """
        Persist only user-configurable, non-ephemeral settings.

        Excludes:
        - running: UI/runtime state
        - version: internal websocket version
        """
        d = self._settings.model_dump()
        d.pop("running", None)
        d.pop("version", None)
        return d

    def get(self) -> RuntimeSettings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def update(self, patch: Dict[str, Any]) -> RuntimeSettings:
        """
        Patch settings via a nested dict shaped like RuntimeSettings.
        """
        with self._lock:
            current = self._settings.model_dump()
            merged = _deep_merge(current, sanitize_settings_patch(patch))
            self._settings = RuntimeSettings.model_validate(merged)
            self._bump_version()
            self._schedule_save()
            return self._settings.model_copy(deep=True)

    def set_running(self, running: bool) -> RuntimeSettings:
        with self._lock:
            self._settings.running = running
            self._bump_version()
            return self._settings.model_copy(deep=True)

    def reset(self) -> RuntimeSettings:
        with self._lock:
            # reset to defaults but keep running state false
            self._settings = self._load_defaults()
            self._settings.running = False
            self._bump_version()
            self._schedule_save()
            return self._settings.model_copy(deep=True)

    def _bump_version(self) -> None:
        # monotonically increasing version for websocket clients
        self._settings.version = int(getattr(self._settings, "version", 0) or 0) + 1


def _deep_merge(base: Any, patch: Any) -> Any:
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            if k in out:
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    return patch

