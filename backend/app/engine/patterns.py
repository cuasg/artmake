from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Dict, Tuple


RGB = Tuple[int, int, int]


def _clamp_u8(x: float) -> int:
    if x <= 0.0:
        return 0
    if x >= 255.0:
        return 255
    return int(x)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def _hash2(ix: int, iy: int) -> float:
    # Deterministic pseudo-random in [0,1)
    n = ix * 374761393 + iy * 668265263  # large primes
    n = (n ^ (n >> 13)) * 1274126177
    n = n ^ (n >> 16)
    return (n & 0xFFFFFFFF) / 2**32


def _value_noise(x: float, y: float) -> float:
    x0 = math.floor(x)
    y0 = math.floor(y)
    x1 = x0 + 1
    y1 = y0 + 1

    sx = _smoothstep(x - x0)
    sy = _smoothstep(y - y0)

    n00 = _hash2(x0, y0)
    n10 = _hash2(x1, y0)
    n01 = _hash2(x0, y1)
    n11 = _hash2(x1, y1)

    ix0 = _lerp(n00, n10, sx)
    ix1 = _lerp(n01, n11, sx)
    return _lerp(ix0, ix1, sy)


def pattern_waves(xn: float, yn: float, t: float) -> RGB:
    # Soft, organic waves with subtle noise modulation.
    a = 0.55 + 0.45 * math.sin(2 * math.pi * (xn * 1.1 + t * 0.06) + 2.2 * math.sin(t * 0.11))
    b = 0.55 + 0.45 * math.sin(2 * math.pi * (yn * 1.3 + t * 0.05) + 1.7 * math.sin(t * 0.09))
    n = _value_noise(xn * 4.0 + t * 0.12, yn * 4.0 + t * 0.08)
    v = 0.6 * a + 0.4 * b
    v = 0.78 * v + 0.22 * n

    # Ambient palette: deep teal -> warm peach highlights
    r = _clamp_u8(20 + 120 * (v**1.6) + 20 * n)
    g = _clamp_u8(30 + 140 * (v**1.1))
    b = _clamp_u8(60 + 170 * (1.0 - abs(v - 0.5) * 1.6))
    return (r, g, b)


def pattern_pulse(xn: float, yn: float, t: float) -> RGB:
    # Soft pulsing gradients radiating from moving centers.
    cx = 0.5 + 0.18 * math.sin(t * 0.07)
    cy = 0.5 + 0.18 * math.cos(t * 0.06)
    dx = xn - cx
    dy = yn - cy
    d = math.sqrt(dx * dx + dy * dy)

    pulse = 0.5 + 0.5 * math.sin(2 * math.pi * (t * 0.08 - d * 1.2))
    haze = _value_noise(xn * 3.2 + t * 0.05, yn * 3.2 - t * 0.04)
    v = 0.75 * pulse + 0.25 * haze

    # Ambient palette: indigo -> lavender
    r = _clamp_u8(25 + 90 * (v**1.4))
    g = _clamp_u8(20 + 80 * (v**1.2))
    b = _clamp_u8(60 + 180 * (v**1.0))
    return (r, g, b)


def pattern_fractal_julia(xn: float, yn: float, t: float) -> RGB:
    """
    Ambient Julia set fractal.

    Kept intentionally low-iteration so it stays usable on a Pi later.
    """
    # Map normalized coords into complex plane
    x = (xn - 0.5) * 3.0
    y = (yn - 0.5) * 3.0

    # Slowly evolving Julia parameter c(t)
    cr = -0.72 + 0.10 * math.cos(t * 0.15)
    ci = 0.18 + 0.10 * math.sin(t * 0.13)

    zr = x
    zi = y
    max_iter = 26

    it = 0
    for i in range(max_iter):
        # z = z^2 + c
        zr2 = zr * zr - zi * zi + cr
        zi2 = 2.0 * zr * zi + ci
        zr, zi = zr2, zi2
        if zr * zr + zi * zi > 4.0:
            it = i
            break
        it = i

    # Smooth-ish intensity; keep it soft/ambient
    v = it / (max_iter - 1)
    v = v**0.7

    # Deep indigo background -> warm highlight edges
    r = _clamp_u8(10 + 210 * (v**2.2))
    g = _clamp_u8(12 + 140 * (v**1.6))
    b = _clamp_u8(30 + 220 * (v**1.1))
    return (r, g, b)


PATTERNS: Dict[str, Callable[[float, float, float], RGB]] = {
    "waves": pattern_waves,
    "pulse": pattern_pulse,
    "fractal_julia": pattern_fractal_julia,
}


@dataclass(frozen=True)
class PatternInfo:
    name: str
    display_name: str
    description: str


PATTERN_INFOS = [
    PatternInfo("waves", "Waves", "Slow flowing waves with subtle noise."),
    PatternInfo("pulse", "Pulse", "Soft pulsing gradients with ambient haze."),
    PatternInfo("fractal_julia", "Fractal (Julia)", "Ambient Julia set fractal field."),
    PatternInfo("living_drawing", "Living Drawing", "Draw an uploaded image as a continuous line."),
]

