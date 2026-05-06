from __future__ import annotations

from typing import List

from app.engine.line_draw import Point, bresenham, expand_path


def dedupe_consecutive(points: List[Point]) -> List[Point]:
    if not points:
        return []
    out: List[PixelPoint] = [points[0]]
    for p in points[1:]:
        if p != out[-1]:
            out.append(p)
    return out


def stitch_gaps_with_bresenham(points: List[Point], max_w: int, max_h: int) -> List[Point]:
    """
    Connect consecutive points with 8-connected segments when they aren't adjacent.
    Clamps every emitted pixel to the matrix bounds.
    """
    if len(points) < 2:
        return [clamp_point(p, max_w, max_h) for p in points]

    out: List[Point] = []
    prev = clamp_point(points[0], max_w, max_h)
    out.append(prev)

    for p in points[1:]:
        cur = clamp_point(p, max_w, max_h)
        if cur == prev:
            continue
        dx = abs(cur[0] - prev[0])
        dy = abs(cur[1] - prev[1])
        if dx <= 1 and dy <= 1:
            out.append(cur)
            prev = cur
            continue
        seg = bresenham(prev, cur)
        # Skip first point (already in out)
        for q in seg[1:]:
            cq = clamp_point(q, max_w, max_h)
            if cq != out[-1]:
                out.append(cq)
        prev = out[-1]
    return out


def clamp_point(p: Point, w: int, h: int) -> Point:
    x = int(p[0])
    y = int(p[1])
    if x < 0:
        x = 0
    elif x >= w:
        x = w - 1
    if y < 0:
        y = 0
    elif y >= h:
        y = h - 1
    return x, y


def downsample_stride(points: List[Point], max_len: int) -> List[Point]:
    if max_len <= 0 or len(points) <= max_len:
        return points
    stride = max(1, len(points) // max_len)
    return points[::stride]


def normalize_ai_toolpath(points: List[Point], w: int, h: int, *, max_len: int = 80000) -> List[Point]:
    pts = dedupe_consecutive(points)
    pts = stitch_gaps_with_bresenham(pts, w, h)
    pts = expand_path(pts)  # 8-connected walk between neighbors
    pts = dedupe_consecutive(pts)
    pts = downsample_stride(pts, max_len)
    return pts
