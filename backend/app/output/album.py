from __future__ import annotations

from typing import List

from app.image_library import ImageLibrary


def album_candidates(lib: ImageLibrary, w: int, h: int) -> List[str]:
    """
    Image IDs that have a saved vectorized toolpath for the current matrix size.
    Sorted by id for stable ordering on disk.
    """
    out: List[str] = []
    for e in lib.list():
        try:
            tps = lib.list_toolpaths(e.id)
        except Exception:
            continue
        ok = False
        for tp in tps:
            if not isinstance(tp, dict):
                continue
            if int(tp.get("w") or 0) != int(w):
                continue
            if int(tp.get("h") or 0) != int(h):
                continue
            if str(tp.get("source") or "").strip().lower() != "vectorized":
                continue
            ok = True
            break
        if ok:
            out.append(e.id)
    return sorted(set(out))


def next_album_id(ids: List[str], current: str | None) -> str | None:
    """Advance to next id in playlist; wrap. Returns None if ids empty."""
    if not ids:
        return None
    if not current or current not in ids:
        return ids[0]
    i = ids.index(current)
    return ids[(i + 1) % len(ids)]
