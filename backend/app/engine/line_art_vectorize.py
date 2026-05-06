from __future__ import annotations

from typing import Dict, List, Set, Tuple

import numpy as np
from PIL import Image, ImageOps, ImageFilter

Point = Tuple[int, int]

def _skeletonize_zhang_suen(bw: np.ndarray, *, max_iters: int = 64) -> np.ndarray:
    """
    Zhang–Suen thinning on a binary image.
    bw: bool array where True = foreground/ink.
    Returns a bool array (skeleton) with mostly 1-pixel-wide strokes.
    """
    img = bw.astype(np.uint8).copy()
    h, w = img.shape
    if h < 3 or w < 3:
        return img.astype(bool)

    def _neighbors(x: int, y: int) -> Tuple[int, int, int, int, int, int, int, int]:
        # P2..P9 clockwise starting at north
        return (
            img[y - 1, x],     # P2
            img[y - 1, x + 1], # P3
            img[y, x + 1],     # P4
            img[y + 1, x + 1], # P5
            img[y + 1, x],     # P6
            img[y + 1, x - 1], # P7
            img[y, x - 1],     # P8
            img[y - 1, x - 1], # P9
        )

    def _transitions(nb: Tuple[int, ...]) -> int:
        # Count 0->1 transitions in the circular sequence P2..P9,P2
        seq = nb + (nb[0],)
        t = 0
        for i in range(8):
            if seq[i] == 0 and seq[i + 1] == 1:
                t += 1
        return t

    it = 0
    changed = True
    while changed and it < max_iters:
        changed = False
        it += 1

        to_remove: List[Tuple[int, int]] = []
        # sub-iteration 1
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                if img[y, x] != 1:
                    continue
                nb = _neighbors(x, y)
                n = sum(nb)
                if n < 2 or n > 6:
                    continue
                if _transitions(nb) != 1:
                    continue
                p2, p3, p4, p5, p6, p7, p8, p9 = nb
                if p2 * p4 * p6 != 0:
                    continue
                if p4 * p6 * p8 != 0:
                    continue
                to_remove.append((x, y))
        if to_remove:
            for x, y in to_remove:
                img[y, x] = 0
            changed = True

        to_remove = []
        # sub-iteration 2
        for y in range(1, h - 1):
            for x in range(1, w - 1):
                if img[y, x] != 1:
                    continue
                nb = _neighbors(x, y)
                n = sum(nb)
                if n < 2 or n > 6:
                    continue
                if _transitions(nb) != 1:
                    continue
                p2, p3, p4, p5, p6, p7, p8, p9 = nb
                if p2 * p4 * p8 != 0:
                    continue
                if p2 * p6 * p8 != 0:
                    continue
                to_remove.append((x, y))
        if to_remove:
            for x, y in to_remove:
                img[y, x] = 0
            changed = True

    return img.astype(bool)


def _otsu_threshold(gray_u8: np.ndarray) -> int:
    # gray_u8: uint8 [H,W]
    hist = np.bincount(gray_u8.reshape(-1), minlength=256).astype(np.float64)
    total = gray_u8.size
    if total <= 0:
        return 128
    prob = hist / total
    omega = np.cumsum(prob)
    mu = np.cumsum(prob * np.arange(256))
    mu_t = mu[-1]

    # Between-class variance
    denom = omega * (1.0 - omega)
    denom[denom == 0] = np.nan
    sigma_b = (mu_t * omega - mu) ** 2 / denom
    t = int(np.nanargmax(sigma_b))
    if t < 16:
        return 16
    if t > 240:
        return 240
    return t


def image_to_strokes_lineart(
    img: Image.Image,
    w: int,
    h: int,
    *,
    threshold: int | None = None,
    invert: bool = False,
    auto_invert: bool = True,
    internal_scale: int = 4,
) -> List[List[Point]]:
    """
    Convert black-on-white line art to stroke polylines by:
    - resizing to W×H
    - binarizing ink pixels
    - walking pixel graph into multiple strokes
    """
    if auto_invert and not invert:
        # If the border is dark, assume white-ink-on-black and invert.
        g0 = img.convert("L")
        a0 = np.asarray(g0).astype(np.uint8)
        if a0.size > 0:
            b = 2
            top = a0[:b, :]
            bot = a0[-b:, :]
            left = a0[:, :b]
            right = a0[:, -b:]
            border_mean = float(np.mean(np.concatenate([top.reshape(-1), bot.reshape(-1), left.reshape(-1), right.reshape(-1)])))
            if border_mean < 96.0:
                invert = True

    if invert:
        img = ImageOps.invert(img.convert("RGB"))

    # Work at higher internal resolution to preserve fine line details, then downsample
    # to a 1-pixel skeleton on the target W×H grid.
    s = int(max(1, internal_scale))
    ww = int(w) * s
    hh = int(h) * s
    g_hi = img.convert("L").resize((ww, hh), Image.BILINEAR)
    a_hi = np.asarray(g_hi).astype(np.uint8)
    t_hi = int(threshold) if threshold is not None else _otsu_threshold(a_hi)
    ink_hi = a_hi <= t_hi

    # Light cleanup to reduce “broken” strokes from anti-aliased inputs:
    # close small gaps before thinning (dilate then erode on the binary mask).
    # Implemented via PIL filters to avoid extra deps.
    try:
        m = (ink_hi.astype(np.uint8) * 255)
        im = Image.fromarray(m, mode="L")
        im = im.filter(ImageFilter.MaxFilter(size=3)).filter(ImageFilter.MinFilter(size=3))
        ink_hi = np.asarray(im).astype(np.uint8) > 0
    except Exception:
        pass

    ink_hi = _skeletonize_zhang_suen(ink_hi, max_iters=96)

    # Downsample skeleton to W×H: if any skeleton pixel exists in the block, keep it.
    ink = np.zeros((h, w), dtype=bool)
    for yy in range(h):
        y0 = yy * s
        y1 = min(hh, (yy + 1) * s)
        for xx in range(w):
            x0 = xx * s
            x1 = min(ww, (xx + 1) * s)
            if np.any(ink_hi[y0:y1, x0:x1]):
                ink[yy, xx] = True

    # Final thinning on target grid (ensures 1-dot width after downsampling).
    ink = _skeletonize_zhang_suen(ink, max_iters=64)

    pts: Set[Point] = set()
    for y in range(h):
        for x in range(w):
            if ink[y, x]:
                pts.add((x, y))

    if not pts:
        return []

    def neighbors8(p: Point) -> List[Point]:
        x, y = p
        out: List[Point] = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                q = (x + dx, y + dy)
                if q in pts:
                    out.append(q)
        return out

    def neighbors4(p: Point) -> List[Point]:
        x, y = p
        out: List[Point] = []
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (x + dx, y + dy)
            if q in pts:
                out.append(q)
        return out

    # Build graph degrees once
    # Prefer 4-connected adjacency for stroke continuity; 8-connected can create
    # lots of tiny junctions on diagonals which fragments strokes.
    nmap4: Dict[Point, List[Point]] = {p: neighbors4(p) for p in pts}
    nmap8: Dict[Point, List[Point]] = {p: neighbors8(p) for p in pts}
    nmap: Dict[Point, List[Point]] = {}
    for p in pts:
        n4 = nmap4[p]
        if n4:
            nmap[p] = n4
        else:
            # Fall back to diagonal connectivity only when a pixel would be isolated.
            nmap[p] = nmap8[p]
    deg: Dict[Point, int] = {p: len(nmap[p]) for p in pts}

    # Stroke decomposition:
    # - "key nodes": endpoints (deg==1) and junctions (deg>=3) start/stop strokes.
    # - traverse edges between key nodes through degree-2 chains.
    key_nodes: Set[Point] = {p for p, d in deg.items() if d != 2}
    visited_edges: Set[Tuple[Point, Point]] = set()
    strokes: List[List[Point]] = []

    def mark_edge(a: Point, b: Point) -> None:
        visited_edges.add((a, b))
        visited_edges.add((b, a))

    def edge_seen(a: Point, b: Point) -> bool:
        return (a, b) in visited_edges

    def trace_edge(start: Point, nxt: Point) -> List[Point]:
        path: List[Point] = [start, nxt]
        mark_edge(start, nxt)
        prev = start
        cur = nxt
        while True:
            if cur in key_nodes and cur != start:
                break
            # degree-2 continuation: pick neighbor not equal prev
            nbs = nmap.get(cur, [])
            candidates = [q for q in nbs if q != prev]
            if not candidates:
                break
            # Prefer an unvisited edge if possible
            candidates.sort(key=lambda q: (edge_seen(cur, q),))
            nxt2 = candidates[0]
            if edge_seen(cur, nxt2):
                # already traced; stop to avoid loops
                break
            path.append(nxt2)
            mark_edge(cur, nxt2)
            prev, cur = cur, nxt2
        return path

    # First, from key nodes (endpoints/junctions)
    for kn in sorted(key_nodes, key=lambda p: (deg.get(p, 0), p[1], p[0])):  # endpoints early
        for nb in nmap.get(kn, []):
            if edge_seen(kn, nb):
                continue
            seg = trace_edge(kn, nb)
            if len(seg) >= 2:
                strokes.append(seg)

    # Remaining cycles (all degree==2): trace any unseen edge loop
    for p in pts:
        for nb in nmap.get(p, []):
            if edge_seen(p, nb):
                continue
            seg = trace_edge(p, nb)
            if len(seg) >= 2:
                strokes.append(seg)

    # Sort: longer strokes first to “lay down silhouette” before details.
    strokes.sort(key=len, reverse=True)
    return strokes

