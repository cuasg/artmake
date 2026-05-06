from __future__ import annotations

import logging
import sys
from typing import Any

from app.models.settings import RuntimeSettings

_LOG = logging.getLogger(__name__)

# Lazily resolved strip-type constants from rpi_ws281x (optional dependency).
_STRIP_TYPES: dict[str, int] = {}
_WS281X_AVAILABLE = False


def _load_ws281x() -> bool:
    global _WS281X_AVAILABLE, _STRIP_TYPES
    if _WS281X_AVAILABLE:
        return True
    try:
        import rpi_ws281x as ws  # type: ignore

        for name in (
            "WS2811_STRIP_RGB",
            "WS2811_STRIP_RBG",
            "WS2811_STRIP_GRB",
            "WS2811_STRIP_GBR",
            "WS2811_STRIP_BRG",
            "WS2811_STRIP_BGR",
            "WS2812_STRIP",
            "SK6812_STRIP",
            "SK6812W_STRIP",
        ):
            if hasattr(ws, name):
                _STRIP_TYPES[name] = int(getattr(ws, name))
        _WS281X_AVAILABLE = True
        return True
    except Exception as e:
        _LOG.warning("rpi_ws281x not available (%s); Raspberry Pi LED output disabled.", e)
        return False


def _strip_type_int(name: str) -> int:
    _load_ws281x()
    key = (name or "WS2812_STRIP").strip().upper()
    if key in _STRIP_TYPES:
        return _STRIP_TYPES[key]
    # Common aliases
    aliases = {
        "WS2812": "WS2812_STRIP",
        "WS2811": "WS2811_STRIP_GRB",
        "SK6812": "SK6812_STRIP",
        "GRB": "WS2811_STRIP_GRB",
        "RGB": "WS2811_STRIP_RGB",
    }
    k2 = aliases.get(key, "")
    if k2 and k2 in _STRIP_TYPES:
        return _STRIP_TYPES[k2]
    return _STRIP_TYPES.get("WS2812_STRIP", 0)


class RaspberryPiWs281xDriver:
    """
    WS281x / NeoPixel-style strips wired as W×H row-major (index = y * W + x).

    Requires `rpi_ws281x` (see requirements-pi.txt). Safe no-op on non-Linux or
    when the library is missing — logs once.
    """

    def __init__(self) -> None:
        self._strip: Any = None
        self._last_key: tuple[Any, ...] | None = None
        self._warned_no_lib = False

    def close(self) -> None:
        self._strip = None
        self._last_key = None

    def _ensure_strip(self, settings: RuntimeSettings, led_count: int) -> Any | None:
        o = settings.output
        if not _load_ws281x():
            if not self._warned_no_lib:
                _LOG.warning(
                    "Install rpi_ws281x on the Raspberry Pi (requirements-pi.txt) for GPIO LED output."
                )
                self._warned_no_lib = True
            return None

        from rpi_ws281x import PixelStrip  # type: ignore

        pin = int(o.pi_gpio_pin)
        freq = int(o.pi_led_freq_hz)
        dma = int(o.pi_led_dma)
        invert = bool(o.pi_invert_signal)
        ch = int(o.pi_led_channel)
        strip_t = _strip_type_int(str(o.pi_strip_type))
        key = (pin, freq, dma, invert, ch, strip_t, led_count)
        if self._strip is not None and self._last_key == key:
            return self._strip

        try:
            strip = PixelStrip(
                led_count,
                pin,
                freq_hz=freq,
                dma=dma,
                invert=invert,
                brightness=int(max(0, min(255, round(float(o.pi_strip_brightness) * 255.0)))),
                channel=ch,
                strip_type=strip_t,
            )
            strip.begin()
            self._strip = strip
            self._last_key = key
            _LOG.info(
                "WS281x strip initialized: %d LEDs, pin=%d dma=%d channel=%d type=%s",
                led_count,
                pin,
                dma,
                ch,
                str(o.pi_strip_type),
            )
            return strip
        except Exception as e:
            _LOG.error("Failed to init WS281x strip: %s", e)
            self._strip = None
            self._last_key = None
            return None

    def push_rgb_frame(self, settings: RuntimeSettings, w: int, h: int, rgb: bytes) -> None:
        o = settings.output
        if str(o.mode) != "raspberry_pi":
            return
        if not sys.platform.startswith("linux"):
            return

        n = int(w) * int(h)
        if len(rgb) != n * 3:
            _LOG.warning("RGB buffer size mismatch: got %d bytes, expected %d", len(rgb), n * 3)
            return

        strip = self._ensure_strip(settings, n)
        if strip is None:
            return

        try:
            from rpi_ws281x import Color  # type: ignore
        except Exception:
            return

        gain = float(o.pi_rgb_gain)
        if gain < 0:
            gain = 0.0
        if gain > 2.0:
            gain = 2.0

        for i in range(n):
            r = int(rgb[i * 3])
            g = int(rgb[i * 3 + 1])
            b = int(rgb[i * 3 + 2])
            if gain != 1.0:
                r = int(min(255, max(0, round(r * gain))))
                g = int(min(255, max(0, round(g * gain))))
                b = int(min(255, max(0, round(b * gain))))
            strip.setPixelColor(i, Color(r, g, b))
        strip.show()
