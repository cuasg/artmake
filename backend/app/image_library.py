from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ImageEntry:
    id: str
    filename: str
    path: Path
    size_bytes: int
    label: str
    crop_focus: str
    parent_id: str | None = None
    kind: str = "original"


_LABEL_MAX = 120


def _sanitize_label(raw: str | None, fallback: str) -> str:
    if raw is None:
        return fallback
    s = raw.strip()
    s = re.sub(r"[\r\n\t]+", " ", s)
    if len(s) > _LABEL_MAX:
        s = s[:_LABEL_MAX].rstrip()
    return s or fallback


class ImageLibrary:
    """
    Simple on-disk image library under data/images/.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.images_dir = root_dir / "data" / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.toolpaths_dir = root_dir / "data" / "toolpaths"
        self.toolpaths_dir.mkdir(parents=True, exist_ok=True)
        self._catalog_path = self.images_dir / "catalog.json"

    def _hash_bytes(self, b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()[:16]

    def _load_catalog(self) -> Dict[str, Any]:
        if not self._catalog_path.exists():
            return {}
        try:
            data = json.loads(self._catalog_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_catalog(self, catalog: Dict[str, Any]) -> None:
        self._catalog_path.parent.mkdir(parents=True, exist_ok=True)
        self._catalog_path.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")

    def _default_label(self, image_id: str, original_stem: str) -> str:
        fb = original_stem if original_stem and original_stem != image_id else f"Drawing · {image_id[:8]}"
        return _sanitize_label(None, fb)[:_LABEL_MAX]

    def _label_for_id(self, image_id: str, original_stem: str = "") -> str:
        catalog = self._load_catalog()
        entry = catalog.get(image_id)
        if isinstance(entry, dict):
            lab = entry.get("label")
            if isinstance(lab, str) and lab.strip():
                return _sanitize_label(lab, self._default_label(image_id, original_stem))
        return self._default_label(image_id, original_stem)

    def _crop_focus_for_id(self, image_id: str) -> str:
        catalog = self._load_catalog()
        entry = catalog.get(image_id)
        if isinstance(entry, dict):
            cf = entry.get("crop_focus")
            if isinstance(cf, str) and cf.strip():
                v = cf.strip().lower()
                if v in ("center", "left", "right", "top", "bottom"):
                    return v
        return "center"

    def _parent_for_id(self, image_id: str) -> str | None:
        catalog = self._load_catalog()
        entry = catalog.get(image_id)
        if isinstance(entry, dict):
            pid = entry.get("parent_id")
            if isinstance(pid, str) and pid.strip():
                return pid.strip()
        return None

    def _kind_for_id(self, image_id: str) -> str:
        catalog = self._load_catalog()
        entry = catalog.get(image_id)
        if isinstance(entry, dict):
            k = entry.get("kind")
            if isinstance(k, str) and k.strip():
                return k.strip()
        return "original"

    def set_label(self, image_id: str, label: str) -> str:
        e = self._entry_from_disk(image_id)
        if not e:
            raise ValueError("Image not found")
        catalog = self._load_catalog()
        clean = _sanitize_label(label, e.label)
        catalog[str(image_id)] = {"label": clean}
        self._save_catalog(catalog)
        return clean

    def set_crop_focus(self, image_id: str, crop_focus: str) -> str:
        e = self._entry_from_disk(image_id)
        if not e:
            raise ValueError("Image not found")
        v = (crop_focus or "").strip().lower()
        if v not in ("center", "left", "right", "top", "bottom"):
            raise ValueError("Invalid crop_focus")
        catalog = self._load_catalog()
        cur = catalog.get(str(image_id)) if isinstance(catalog.get(str(image_id)), dict) else {}
        if not isinstance(cur, dict):
            cur = {}
        cur = {**cur, "crop_focus": v}
        catalog[str(image_id)] = cur
        self._save_catalog(catalog)
        return v

    def _entry_from_disk(self, image_id: str) -> Optional[ImageEntry]:
        for p in sorted(self.images_dir.glob("*")):
            if not p.is_file():
                continue
            if p.name == "catalog.json":
                continue
            if p.stem != image_id:
                continue
            return self._to_entry(p)
        return None

    def _to_entry(self, path: Path) -> ImageEntry:
        img_id = path.stem
        label = self._label_for_id(img_id, "")
        crop_focus = self._crop_focus_for_id(img_id)
        parent_id = self._parent_for_id(img_id)
        kind = self._kind_for_id(img_id)
        return ImageEntry(
            id=img_id,
            filename=path.name,
            path=path,
            size_bytes=path.stat().st_size,
            label=label,
            crop_focus=crop_focus,
            parent_id=parent_id,
            kind=kind,
        )

    def save_upload(
        self,
        original_filename: str,
        content: bytes,
        label: str | None = None,
        *,
        crop_focus: str | None = None,
        parent_id: str | None = None,
        kind: str | None = None,
    ) -> ImageEntry:
        img_id = self._hash_bytes(content)
        safe_name = os.path.basename(original_filename).replace("\\", "_").replace("/", "_")
        ext = Path(safe_name).suffix.lower()
        if ext not in [".png", ".jpg", ".jpeg", ".webp", ".bmp"]:
            # default to png; we keep the bytes as-is for now
            ext = ".img"
        filename = f"{img_id}{ext}"
        path = self.images_dir / filename
        stem_guess = Path(safe_name).stem or ""
        proposed = _sanitize_label(label, self._default_label(img_id, stem_guess))
        cf = (crop_focus or "").strip().lower() if crop_focus is not None else None
        if cf is not None and cf not in ("center", "left", "right", "top", "bottom"):
            cf = "center"
        parent_clean = (parent_id or "").strip() or None
        kind_clean = (kind or "").strip() or "original"

        if not path.exists():
            path.write_bytes(content)
            catalog = self._load_catalog()
            meta: Dict[str, Any] = {"label": proposed}
            if cf:
                meta["crop_focus"] = cf
            if parent_clean:
                meta["parent_id"] = parent_clean
            if kind_clean:
                meta["kind"] = kind_clean
            catalog[img_id] = meta
            self._save_catalog(catalog)
        elif label is not None and label.strip():
            # Same bytes re-uploaded: refresh display name if user supplied one.
            catalog = self._load_catalog()
            cur = catalog.get(img_id) if isinstance(catalog.get(img_id), dict) else {}
            if not isinstance(cur, dict):
                cur = {}
            cur["label"] = proposed
            if cf:
                cur["crop_focus"] = cf
            if parent_clean:
                cur["parent_id"] = parent_clean
            if kind_clean:
                cur["kind"] = kind_clean
            catalog[img_id] = cur
            self._save_catalog(catalog)

        return self._to_entry(path)

    def list(self) -> List[ImageEntry]:
        out: List[ImageEntry] = []
        for p in sorted(self.images_dir.glob("*")):
            if not p.is_file():
                continue
            if p.name == "catalog.json":
                continue
            out.append(self._to_entry(p))
        return out

    def get(self, image_id: str) -> Optional[ImageEntry]:
        return self._entry_from_disk(image_id)

    def toolpath_path_for(
        self,
        image_id: str,
        w: int | None = None,
        h: int | None = None,
        source: str | None = None,
    ) -> Path:
        """
        Toolpaths are stored per (image_id, matrix size).
        - New format: {image_id}__{w}x{h}.json
        - Legacy format: {image_id}.json
        """
        if w and h and source:
            return self.toolpaths_dir / f"{image_id}__{int(w)}x{int(h)}__{source}.json"
        if w and h:
            return self.toolpaths_dir / f"{image_id}__{int(w)}x{int(h)}__ai.json"
        return self.toolpaths_dir / f"{image_id}.json"

    def list_toolpaths(self, image_id: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        # new format
        for p in sorted(self.toolpaths_dir.glob(f"{image_id}__*x*__*.json")):
            if not p.is_file():
                continue
            # filename: id__{w}x{h}__{source}.json
            try:
                suffix = p.stem.split("__", 1)[1]
                dim, source = suffix.split("__", 1)
                w_s, h_s = dim.split("x", 1)
                w = int(w_s)
                h = int(h_s)
            except Exception:
                continue
            meta: Dict[str, Any] = {"w": w, "h": h, "source": source, "path": str(p)}
            try:
                j = json.loads(p.read_text(encoding="utf-8"))
                pts = j.get("expanded_points")
                strokes = j.get("expanded_strokes")
                meta["points"] = (
                    sum(len(s) for s in strokes) if isinstance(strokes, list) else (len(pts) if isinstance(pts, list) else 0)
                )
                meta["strokes"] = len(strokes) if isinstance(strokes, list) else (1 if isinstance(pts, list) else 0)
                meta["model"] = j.get("model") if isinstance(j, dict) else None
            except Exception:
                meta["points"] = 0
                meta["strokes"] = 0
            out.append(meta)

        # legacy fallback
        legacy = self.toolpath_path_for(image_id)
        if legacy.exists():
            try:
                j = json.loads(legacy.read_text(encoding="utf-8"))
                mx = j.get("matrix") if isinstance(j, dict) else None
                w = int(mx.get("width")) if isinstance(mx, dict) and mx.get("width") is not None else None
                h = int(mx.get("height")) if isinstance(mx, dict) and mx.get("height") is not None else None
                if w and h and not any(tp.get("w") == w and tp.get("h") == h for tp in out):
                    out.append({"w": w, "h": h, "legacy": True, "points": 0, "strokes": 0})
            except Exception:
                pass
        return out

    def has_toolpath(self, image_id: str, w: int | None = None, h: int | None = None) -> bool:
        if w and h:
            return self.toolpath_path_for(image_id, w, h, "ai").exists()
        if list(self.toolpaths_dir.glob(f"{image_id}__*x*__*.json")):
            return True
        return self.toolpath_path_for(image_id).exists()

    def load_toolpath(
        self, image_id: str, w: int | None = None, h: int | None = None, source: str | None = None
    ) -> Optional[Dict[str, Any]]:
        p = self.toolpath_path_for(image_id, w, h, source)
        if not p.exists():
            # Legacy fallback
            if w and h:
                p2 = self.toolpath_path_for(image_id)
                if not p2.exists():
                    return None
                p = p2
            else:
                return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save_toolpath(
        self,
        image_id: str,
        obj: Dict[str, Any],
        w: int | None = None,
        h: int | None = None,
        source: str | None = None,
    ) -> None:
        p = self.toolpath_path_for(image_id, w, h, source)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=2), encoding="utf-8")

    def delete_toolpath(
        self,
        image_id: str,
        w: int | None = None,
        h: int | None = None,
        source: str | None = None,
    ) -> bool:
        removed = False
        if w and h:
            p = self.toolpath_path_for(image_id, w, h, source or "ai")
            if p.exists():
                p.unlink()
                removed = True
            return removed

        # remove all
        for p in self.toolpaths_dir.glob(f"{image_id}__*x*__*.json"):
            try:
                if p.is_file():
                    p.unlink()
                    removed = True
            except Exception:
                pass
        legacy = self.toolpath_path_for(image_id)
        if legacy.exists():
            legacy.unlink()
            removed = True
        return removed

    def delete_image(self, image_id: str) -> None:
        e = self._entry_from_disk(image_id)
        if not e:
            raise ValueError("Image not found")
        self.delete_toolpath(image_id)
        try:
            e.path.unlink(missing_ok=True)
        except OSError:
            pass
        catalog = self._load_catalog()
        catalog.pop(str(image_id), None)
        self._save_catalog(catalog)

