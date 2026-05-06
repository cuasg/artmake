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
from app.engine.path_stitch import normalize_ai_strokes, normalize_ai_toolpath
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
        self.drawing.stroke_idx = 0
        self.drawing.point_idx = 0
        self.drawing.flat_idx = 0
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
                program = self._load_drawing_program(entry.path, drawing_id, w, h, source=settings.art.toolpath_source)
                self.drawing.program = program
                self.drawing.stroke_idx = 0
                self.drawing.point_idx = 0
                self.drawing.flat_idx = 0
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

            remaining = step
            while remaining > 0 and self.drawing.program and self.drawing.stroke_idx < len(self.drawing.program.strokes):
                stroke = self.drawing.program.strokes[self.drawing.stroke_idx]
                if self.drawing.point_idx >= len(stroke):
                    self.drawing.stroke_idx += 1
                    self.drawing.point_idx = 0
                    continue
                x, y = stroke[self.drawing.point_idx]
                self.drawing.point_idx += 1
                self.drawing.flat_idx = min(len(self.drawing.program.flat_points), self.drawing.flat_idx + 1)
                remaining -= 1
                if 0 <= x < w and 0 <= y < h:
                    j = (y * w + x) * 3
                    self._canvas_rgb[j] = r
                    self._canvas_rgb[j + 1] = g
                    self._canvas_rgb[j + 2] = b

            # Done drawing all strokes
            if self.drawing.program and self.drawing.stroke_idx >= len(self.drawing.program.strokes):
                self.drawing.mode = "hold"
                self.drawing.mode_started_at_s = now

        elif self.drawing.mode == "hold":
            if now - self.drawing.mode_started_at_s >= self.drawing.hold_s:
                self.drawing.mode = "erase"
                self.drawing.mode_started_at_s = now

        elif self.drawing.mode == "erase":
            step = int(self.drawing.erase_pps * (1.0 / max(1.0, settings.stream.fps)))
            step = max(1, step)
            if not self.drawing.program:
                return w, h, bytes(self._canvas_rgb)
            # erase from end backwards using flattened points
            start_idx = max(0, self.drawing.flat_idx - step)
            for i in range(start_idx, self.drawing.flat_idx):
                x, y = self.drawing.program.flat_points[i]
                if 0 <= x < w and 0 <= y < h:
                    j = (y * w + x) * 3
                    self._canvas_rgb[j] = 0
                    self._canvas_rgb[j + 1] = 0
                    self._canvas_rgb[j + 2] = 0
            self.drawing.flat_idx = start_idx
            if self.drawing.flat_idx <= 0:
                # loop: redraw same image for now
                self.drawing.mode = "draw"
                self.drawing.mode_started_at_s = now
                self.drawing.stroke_idx = 0
                self.drawing.point_idx = 0

        return w, h, bytes(self._canvas_rgb)

    def _load_drawing_program(self, image_path: Path, image_id: str, w: int, h: int, source: str = "auto") -> DrawingProgram:
        lib = self._image_library
        source = (source or "auto").strip().lower()
        preferred = []
        if source == "ai":
            preferred = ["ai"]
        elif source == "vectorized":
            preferred = ["vectorized"]
        else:
            preferred = ["ai", "vectorized"]

        stored: Dict[str, Any] | None = None
        if lib:
            for src in preferred:
                stored = lib.load_toolpath(image_id, w, h, src)
                if stored:
                    break

            # Fallback: if the exact size variant doesn't exist yet, try another saved size
            # and scale it to the requested matrix. This makes matrix switching feel better
            # while variants are being generated.
            if stored is None:
                try:
                    variants = lib.list_toolpaths(image_id)
                except Exception:
                    variants = []
                # Only consider preferred sources
                candidates = [
                    v
                    for v in variants
                    if isinstance(v, dict)
                    and int(v.get("w") or 0) > 0
                    and int(v.get("h") or 0) > 0
                    and (v.get("source") in preferred)
                ]
                if candidates:
                    def score(vv: dict) -> float:
                        vw = float(vv.get("w") or 1)
                        vh = float(vv.get("h") or 1)
                        # favor closest aspect ratio, then closest area
                        ar = abs((vw / vh) - (float(w) / float(h)))
                        area = abs((vw * vh) - (float(w) * float(h))) / max(1.0, float(w) * float(h))
                        return ar * 3.0 + area

                    best = sorted(candidates, key=score)[0]
                    bw = int(best.get("w"))
                    bh = int(best.get("h"))
                    bsrc = str(best.get("source"))
                    stored = lib.load_toolpath(image_id, bw, bh, bsrc)
                    if isinstance(stored, dict):
                        stored = {**stored, "_scaled_from": {"w": bw, "h": bh, "source": bsrc}}
        if isinstance(stored, dict):
            scaled_from = stored.get("_scaled_from") if isinstance(stored.get("_scaled_from"), dict) else None
            sw = int(scaled_from.get("w")) if scaled_from else w
            sh = int(scaled_from.get("h")) if scaled_from else h
            sx = float(w) / float(sw) if sw else 1.0
            sy = float(h) / float(sh) if sh else 1.0

            def _scale_pts(arr: list) -> list:
                out: list = []
                for pt in arr:
                    if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                        continue
                    x = int(round(float(pt[0]) * sx))
                    y = int(round(float(pt[1]) * sy))
                    out.append([x, y])
                return out

            raw_strokes = stored.get("expanded_strokes")
            if isinstance(raw_strokes, list) and raw_strokes:
                if scaled_from:
                    raw_strokes = [_scale_pts(s) for s in raw_strokes if isinstance(s, list)]
                strokes = self._strokes_from_json(raw_strokes)
                if strokes:
                    flat = [p for s in strokes for p in s]
                    return DrawingProgram(strokes=strokes, flat_points=flat, width=w, height=h)
            raw_pts = stored.get("expanded_points")
            if isinstance(raw_pts, list) and raw_pts:
                if scaled_from:
                    raw_pts = _scale_pts(raw_pts)
                pts = self._points_from_json_list(raw_pts)
                if pts:
                    return DrawingProgram(strokes=[pts], flat_points=pts, width=w, height=h)
            raw_obj = stored.get("raw")
            if isinstance(raw_obj, dict):
                raw_s = raw_obj.get("strokes")
                if isinstance(raw_s, list) and raw_s:
                    strokes2 = self._strokes_from_raw(raw_s)
                    if strokes2:
                        norm = normalize_ai_strokes(strokes2, w, h)
                        flat = [p for s in norm for p in s]
                        return DrawingProgram(strokes=norm, flat_points=flat, width=w, height=h)
                raw_path = raw_obj.get("path")
                if isinstance(raw_path, list) and raw_path:
                    pts2 = self._points_from_ai_path(raw_path)
                    if pts2:
                        norm = normalize_ai_toolpath(pts2, w, h)
                        return DrawingProgram(strokes=[norm], flat_points=norm, width=w, height=h)

        pts = image_to_points(image_path, w, h, threshold=0.22)
        ordered = order_points_nearest(pts, max_len=12000)
        expanded = expand_path(ordered)
        return DrawingProgram(strokes=[expanded], flat_points=expanded, width=w, height=h)

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

    def _strokes_from_json(self, raw: List[Any]) -> List[List[Tuple[int, int]]]:
        out: List[List[Tuple[int, int]]] = []
        for s in raw:
            if not isinstance(s, list):
                continue
            pts = self._points_from_json_list(s)
            if len(pts) >= 2:
                out.append(pts)
        return out

    def _strokes_from_raw(self, raw_strokes: List[Any]) -> List[List[Tuple[int, int]]]:
        out: List[List[Tuple[int, int]]] = []
        for s in raw_strokes:
            if not isinstance(s, dict):
                continue
            pts = s.get("points")
            if not isinstance(pts, list):
                continue
            p2 = self._points_from_ai_path(pts)
            if len(p2) >= 2:
                out.append(p2)
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

