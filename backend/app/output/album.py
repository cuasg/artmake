from __future__ import annotations

from typing import List

from app.image_library import ImageEntry, ImageLibrary


def _has_vectorized(lib: ImageLibrary, image_id: str, w: int, h: int) -> bool:
    try:
        tps = lib.list_toolpaths(image_id)
    except Exception:
        return False
    for tp in tps:
        if not isinstance(tp, dict):
            continue
        if int(tp.get("w") or 0) != int(w):
            continue
        if int(tp.get("h") or 0) != int(h):
            continue
        if str(tp.get("source") or "").strip().lower() != "vectorized":
            continue
        return True
    return False


def _vector_source_for_root(lib: ImageLibrary, root: ImageEntry, entries: List[ImageEntry], w: int, h: int) -> str | None:
    """
    Match Gallery UX: prefer the first ai_lineart child (by stable id sort) when it has
    vectorized paths for this matrix; otherwise use the root if it does.

    Returns None if neither side has vectorized (w,h).
    """
    ai_children = sorted(
        [e for e in entries if e.parent_id == root.id and str(e.kind or "").strip().lower() == "ai_lineart"],
        key=lambda e: e.id,
    )
    if ai_children and _has_vectorized(lib, ai_children[0].id, w, h):
        return ai_children[0].id
    if _has_vectorized(lib, root.id, w, h):
        return root.id
    return None


def album_candidates(lib: ImageLibrary, w: int, h: int) -> List[str]:
    """
    One playlist slot per **top-level** (root) upload — aligned with Gallery cards.

    Uses the same effective drawing source as the gallery preview row:
    AI line-art child first when it has a saved vectorized path for (w,h), otherwise the root.

    This excludes orphaned derivatives-only traversal that treated roots + AI copies as separate
    album tracks when paths existed on both, which showed drawings users didn't expect.
    """
    ww, hh = int(w), int(h)
    entries = sorted(lib.list(), key=lambda e: e.id)
    roots = sorted([e for e in entries if not e.parent_id], key=lambda e: e.id)
    out: List[str] = []
    for root in roots:
        vid = _vector_source_for_root(lib, root, entries, ww, hh)
        if vid:
            out.append(vid)
    return out


def next_album_id(ids: List[str], current: str | None) -> str | None:
    """Advance to next id in playlist; wrap. Returns None if ids empty."""
    if not ids:
        return None
    if not current or current not in ids:
        return ids[0]
    i = ids.index(current)
    return ids[(i + 1) % len(ids)]
