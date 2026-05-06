from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image


Point = Tuple[int, int]


def _resize_grayscale(img: Image.Image, w: int, h: int) -> np.ndarray:
    g = img.convert("L").resize((w, h), Image.BILINEAR)
    return (np.asarray(g).astype(np.float32) / 255.0)


def _edge_map(gray: np.ndarray) -> np.ndarray:
    # Simple Sobel-ish gradient magnitude (no external deps).
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:-1] = (gray[:, 2:] - gray[:, :-2]) * 0.5
    gy[1:-1, :] = (gray[2:, :] - gray[:-2, :]) * 0.5
    mag = np.sqrt(gx * gx + gy * gy)
    # Normalize
    mmax = float(np.max(mag)) if mag.size else 1.0
    if mmax <= 1e-6:
        return mag
    return mag / mmax


def image_to_points(image_path: Path, w: int, h: int, threshold: float = 0.22) -> List[Point]:
    img = Image.open(image_path)
    gray = _resize_grayscale(img, w, h)
    edges = _edge_map(gray)
    ys, xs = np.where(edges >= threshold)
    pts: List[Point] = [(int(x), int(y)) for x, y in zip(xs.tolist(), ys.tolist())]
    return pts


def order_points_nearest(pts: List[Point], max_len: int = 20000) -> List[Point]:
    """
    Greedy nearest-neighbor ordering to form a single continuous-ish polyline.
    This is a simple baseline; we can upgrade later (contours/thinning/TSP-ish).
    """
    if not pts:
        return []
    if len(pts) > max_len:
        # Downsample deterministically by stride for performance.
        stride = max(1, len(pts) // max_len)
        pts = pts[::stride]

    remaining = pts[:]
    path: List[Point] = []

    # Start near center for nicer reveals
    cx = sum(p[0] for p in remaining) / len(remaining)
    cy = sum(p[1] for p in remaining) / len(remaining)
    start_i = min(range(len(remaining)), key=lambda i: (remaining[i][0] - cx) ** 2 + (remaining[i][1] - cy) ** 2)
    current = remaining.pop(start_i)
    path.append(current)

    while remaining:
        x0, y0 = current
        # naive nearest search
        best_i = 0
        best_d = 1e18
        for i, (x, y) in enumerate(remaining):
            d = (x - x0) * (x - x0) + (y - y0) * (y - y0)
            if d < best_d:
                best_d = d
                best_i = i
        current = remaining.pop(best_i)
        path.append(current)

    return path


def bresenham(a: Point, b: Point) -> List[Point]:
    x0, y0 = a
    x1, y1 = b
    points: List[Point] = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        points.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy
    return points


def expand_path(path: List[Point]) -> List[Point]:
    if len(path) < 2:
        return path
    out: List[Point] = [path[0]]
    for i in range(1, len(path)):
        seg = bresenham(path[i - 1], path[i])
        out.extend(seg[1:])
    return out


@dataclass
class DrawingProgram:
    # strokes are drawn sequentially to simulate brush strokes.
    strokes: List[List[Point]]
    # flattened path for fast erase and legacy compatibility.
    flat_points: List[Point]
    width: int
    height: int


@dataclass
class DrawingState:
    program: Optional[DrawingProgram] = None
    stroke_idx: int = 0
    point_idx: int = 0
    flat_idx: int = 0  # how many flat_points have been drawn (for erase)
    mode: str = "idle"  # draw|hold|erase|idle
    mode_started_at_s: float = 0.0
    hold_s: float = 4.0
    draw_pps: float = 250.0  # points-per-second
    erase_pps: float = 800.0

    def load_program(self, program: DrawingProgram) -> None:
        self.program = program
        self.stroke_idx = 0
        self.point_idx = 0
        self.flat_idx = 0
        self.mode = "draw"
        self.mode_started_at_s = 0.0

