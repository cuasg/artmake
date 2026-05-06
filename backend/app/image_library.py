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

    def set_label(self, image_id: str, label: str) -> str:
        e = self._entry_from_disk(image_id)
        if not e:
            raise ValueError("Image not found")
        catalog = self._load_catalog()
        clean = _sanitize_label(label, e.label)
        catalog[str(image_id)] = {"label": clean}
        self._save_catalog(catalog)
        return clean

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
        return ImageEntry(
            id=img_id,
            filename=path.name,
            path=path,
            size_bytes=path.stat().st_size,
            label=label,
        )

    def save_upload(self, original_filename: str, content: bytes, label: str | None = None) -> ImageEntry:
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

        if not path.exists():
            path.write_bytes(content)
            catalog = self._load_catalog()
            catalog[img_id] = {"label": proposed}
            self._save_catalog(catalog)
        elif label is not None and label.strip():
            # Same bytes re-uploaded: refresh display name if user supplied one.
            catalog = self._load_catalog()
            catalog[img_id] = {"label": proposed}
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

    def toolpath_path_for(self, image_id: str) -> Path:
        return self.toolpaths_dir / f"{image_id}.json"

    def has_toolpath(self, image_id: str) -> bool:
        return self.toolpath_path_for(image_id).exists()

    def load_toolpath(self, image_id: str) -> Optional[Dict[str, Any]]:
        p = self.toolpath_path_for(image_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def save_toolpath(self, image_id: str, obj: Dict[str, Any]) -> None:
        p = self.toolpath_path_for(image_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=2), encoding="utf-8")

    def delete_toolpath(self, image_id: str) -> bool:
        p = self.toolpath_path_for(image_id)
        if not p.exists():
            return False
        p.unlink()
        return True

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

