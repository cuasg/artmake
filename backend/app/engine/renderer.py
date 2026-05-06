from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.engine.patterns import PATTERNS
from app.models.settings import RuntimeSettings
from app.engine.line_draw import (
    DrawingProgram,
    DrawingState,
    expand_path,
    image_to_points,
    order_points_nearest,
)
from app.engine.path_stitch import normalize_ai_toolpath
from app.image_library import ImageLibrary


def _scale_u8(c: int, brightness: float) -> int:
    v = int(c * brightness)
    if v < 0:
        return 0
    if v > 255:
        return 255
    return v


@dataclass
class RenderState:
    t0: float = time.time()
    t_offset: float = 0.0
    active_pattern: str = "waves"
    prev_pattern: str | None = None
    transition_started_at: float = 0.0
    transition_duration_s: float = 1.1

    def reset(self) -> None:
        self.t0 = time.time()
        self.t_offset = 0.0
        self.active_pattern = "waves"
        self.prev_pattern = None
        self.transition_started_at = 0.0

    def now_t(self, speed: float) -> float:
        # speed is a multiplier; 1.0 is baseline “ambient”.
        return (time.time() - self.t0) * speed + self.t_offset

    def transition_alpha(self) -> float:
        if not self.prev_pattern:
            return 1.0
        dt = time.time() - self.transition_started_at
        if dt <= 0:
            return 0.0
        if dt >= self.transition_duration_s:
            self.prev_pattern = None
            return 1.0
        x = dt / self.transition_duration_s
        # smoothstep
        return x * x * (3.0 - 2.0 * x)


class FrameRenderer:
    def __init__(self, image_library: ImageLibrary | None = None) -> None:
        self.state = RenderState()
        self.drawing = DrawingState()
        self._image_library = image_library
        self._drawing_cached_id: str | None = None
        self._canvas_rgb: bytearray | None = None

    def reset(self) -> None:
        self.state.reset()
        self.drawing = DrawingState()
        self._drawing_cached_id = None
        self._canvas_rgb = None

    def invalidate_living_drawing(self, image_id: str | None = None) -> None:
        """
        Force reloading the drawing program the next time living_drawing renders.
        If image_id is provided, only invalidate when that drawing is active.
        """
        if image_id and self._drawing_cached_id and self._drawing_cached_id != image_id:
            return
        self._drawing_cached_id = None
        self.drawing.program = None
        self.drawing.idx = 0
        self.drawing.mode = "idle"

    def render_rgb_bytes(self, settings: RuntimeSettings) -> tuple[int, int, bytes]:
        w = settings.matrix.width
        h = settings.matrix.height

        # Living drawing mode: a toolpath that accumulates on a persistent canvas.
        if settings.art.pattern == "living_drawing":
            return self._render_living_drawing(settings)

        t = self.state.now_t(settings.art.speed)
        brightness = settings.art.brightness

        # Pattern transitions (soft cross-fade)
        requested = settings.art.pattern
        if requested != self.state.active_pattern:
            self.state.prev_pattern = self.state.active_pattern
            self.state.active_pattern = requested
            self.state.transition_started_at = time.time()

        new_fn = PATTERNS.get(self.state.active_pattern, PATTERNS["waves"])
        old_fn = PATTERNS.get(self.state.prev_pattern, PATTERNS["waves"]) if self.state.prev_pattern else None
        alpha = self.state.transition_alpha()

        buf = bytearray(w * h * 3)
        i = 0
        for y in range(h):
            yn = y / max(1, h - 1)
            for x in range(w):
                xn = x / max(1, w - 1)
                r, g, b = new_fn(xn, yn, t)
                if old_fn and alpha < 1.0:
                    r0, g0, b0 = old_fn(xn, yn, t)
                    r = int(r0 + (r - r0) * alpha)
                    g = int(g0 + (g - g0) * alpha)
                    b = int(b0 + (b - b0) * alpha)

                buf[i] = _scale_u8(r, brightness)
                buf[i + 1] = _scale_u8(g, brightness)
                buf[i + 2] = _scale_u8(b, brightness)
                i += 3

        return w, h, bytes(buf)

    def _render_living_drawing(self, settings: RuntimeSettings) -> tuple[int, int, bytes]:
        w = settings.matrix.width
        h = settings.matrix.height

        if self._canvas_rgb is None or len(self._canvas_rgb) != w * h * 3:
            self._canvas_rgb = bytearray(w * h * 3)
            for i in range(0, len(self._canvas_rgb), 3):
                self._canvas_rgb[i] = 0
                self._canvas_rgb[i + 1] = 0
                self._canvas_rgb[i + 2] = 0

        drawing_id = settings.art.drawing_id
        if drawing_id and drawing_id != self._drawing_cached_id and self._image_library:
            entry = self._image_library.get(drawing_id)
            if entry:
                expanded = self._load_drawing_program_points(entry.path, drawing_id, w, h)
                self.drawing.program = DrawingProgram(points=expanded, width=w, height=h)
                self.drawing.idx = 0
                self.drawing.mode = "draw"
                self.drawing.mode_started_at_s = time.time()
                self.drawing.hold_s = float(settings.art.hold_seconds)
                self.drawing.draw_pps = float(settings.art.draw_pps)
                self.drawing.erase_pps = float(settings.art.erase_pps)
                self._drawing_cached_id = drawing_id

                # Clear canvas on new drawing
                for i in range(0, len(self._canvas_rgb), 3):
                    self._canvas_rgb[i] = 0
                    self._canvas_rgb[i + 1] = 0
                    self._canvas_rgb[i + 2] = 0

        # No program yet: return blank
        if not self.drawing.program:
            return w, h, bytes(self._canvas_rgb)

        now = time.time()
        dt = max(0.0, now - (self.drawing.mode_started_at_s or now))

        # Color
        r, g, b = _hex_to_rgb(settings.art.line_color)
        r = _scale_u8(r, settings.art.brightness)
        g = _scale_u8(g, settings.art.brightness)
        b = _scale_u8(b, settings.art.brightness)

        if self.drawing.mode == "draw":
            step = int(self.drawing.draw_pps * (1.0 / max(1.0, settings.stream.fps)))
            step = max(1, step)
            end = min(len(self.drawing.program.points), self.drawing.idx + step)
            for i in range(self.drawing.idx, end):
                x, y = self.drawing.program.points[i]
                if 0 <= x < w and 0 <= y < h:
                    j = (y * w + x) * 3
                    self._canvas_rgb[j] = r
                    self._canvas_rgb[j + 1] = g
                    self._canvas_rgb[j + 2] = b
            self.drawing.idx = end
            if self.drawing.idx >= len(self.drawing.program.points) - 1:
                self.drawing.mode = "hold"
                self.drawing.mode_started_at_s = now

        elif self.drawing.mode == "hold":
            if now - self.drawing.mode_started_at_s >= self.drawing.hold_s:
                self.drawing.mode = "erase"
                self.drawing.mode_started_at_s = now

        elif self.drawing.mode == "erase":
            step = int(self.drawing.erase_pps * (1.0 / max(1.0, settings.stream.fps)))
            step = max(1, step)
            # erase from end backwards
            start_idx = max(0, self.drawing.idx - step)
            for i in range(start_idx, self.drawing.idx):
                x, y = self.drawing.program.points[i]
                if 0 <= x < w and 0 <= y < h:
                    j = (y * w + x) * 3
                    self._canvas_rgb[j] = 0
                    self._canvas_rgb[j + 1] = 0
                    self._canvas_rgb[j + 2] = 0
            self.drawing.idx = start_idx
            if self.drawing.idx <= 0:
                # loop: redraw same image for now
                self.drawing.mode = "draw"
                self.drawing.mode_started_at_s = now

        return w, h, bytes(self._canvas_rgb)

    def _load_drawing_program_points(self, image_path: Path, image_id: str, w: int, h: int) -> List[Tuple[int, int]]:
        lib = self._image_library
        stored: Dict[str, Any] | None = lib.load_toolpath(image_id) if lib else None
        if isinstance(stored, dict):
            raw_pts = stored.get("expanded_points")
            if isinstance(raw_pts, list) and raw_pts:
                pts = self._points_from_json_list(raw_pts)
                if pts:
                    return pts
            raw_obj = stored.get("raw")
            if isinstance(raw_obj, dict):
                raw_path = raw_obj.get("path")
                if isinstance(raw_path, list) and raw_path:
                    pts2 = self._points_from_ai_path(raw_path)
                    if pts2:
                        return normalize_ai_toolpath(pts2, w, h)

        pts = image_to_points(image_path, w, h, threshold=0.22)
        ordered = order_points_nearest(pts, max_len=12000)
        return expand_path(ordered)

    def _points_from_json_list(self, raw_pts: List[Any]) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for pt in raw_pts:
            if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                continue
            out.append((int(pt[0]), int(pt[1])))
        return out

    def _points_from_ai_path(self, path: List[Any]) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for pt in path:
            if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                continue
            out.append((int(pt[0]), int(pt[1])))
        return out


def _hex_to_rgb(s: str) -> tuple[int, int, int]:
    try:
        t = s.strip()
        if t.startswith("#"):
            t = t[1:]
        if len(t) == 3:
            t = "".join([c * 2 for c in t])
        if len(t) != 6:
            return (184, 215, 255)
        r = int(t[0:2], 16)
        g = int(t[2:4], 16)
        b = int(t[4:6], 16)
        return (r, g, b)
    except Exception:
        return (184, 215, 255)

