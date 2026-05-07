from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


MatrixPreset = str  # keep permissive for backward-compatible config loads
PatternName = Literal["waves", "pulse", "fractal_julia", "living_drawing", "pixel_media", "camera_mirror"]
LedShape = Literal["circle", "square"]
OutputMode = Literal["simulator", "raspberry_pi"]
PhotoPlaybackMode = Literal["selected", "album"]


class MatrixSettings(BaseModel):
    width: int = Field(default=64, ge=1, le=512)
    height: int = Field(default=64, ge=1, le=512)
    preset: Optional[MatrixPreset] = None

    @model_validator(mode="after")
    def _apply_preset(self) -> "MatrixSettings":
        if not self.preset:
            return self
        p = str(self.preset).strip().lower()
        allowed = {"32x32", "64x64", "64x96"}
        if p not in allowed:
            # Ignore legacy/unsupported presets instead of failing to load.
            self.preset = None
            return self
        w, h = p.split("x", 1)
        self.width = int(w)
        self.height = int(h)
        return self


class StreamSettings(BaseModel):
    fps: int = Field(default=30, ge=1, le=120)
    auto_fps: bool = True
    max_fps: int = Field(default=60, ge=1, le=120)
    auto_learn: bool = True


class ArtSettings(BaseModel):
    pattern: PatternName = "waves"
    speed: float = Field(default=1.0, ge=0.0, le=5.0)
    brightness: float = Field(default=0.65, ge=0.0, le=1.0)
    drawing_id: Optional[str] = None
    draw_pps: float = Field(default=250.0, ge=10.0, le=5000.0)
    hold_seconds: float = Field(default=4.0, ge=0.0, le=60.0)
    erase_pps: float = Field(default=800.0, ge=10.0, le=20000.0)
    line_color: str = Field(default="#b8d7ff")  # css hex
    toolpath_source: Literal["auto", "ai", "vectorized"] = "auto"
    # Full-color media modes (pixel_media / camera_mirror)
    media_filter: Literal["none", "pixel", "pixel_dither", "mono"] = "pixel"
    media_fps_cap: int = Field(default=15, ge=1, le=60)
    camera_fps_cap: int = Field(default=10, ge=1, le=30)


class SimulatorSettings(BaseModel):
    led_shape: LedShape = "circle"
    led_spacing: int = Field(default=1, ge=0, le=10)
    glow: float = Field(default=0.25, ge=0.0, le=1.0)


class OutputSettings(BaseModel):
    """
    `simulator`: frames only go to the browser WebSocket (default for desktop testing).

    `raspberry_pi`: same frames also pushed to a WS281x LED strip / matrix (see Pi docs).
    """

    mode: OutputMode = "simulator"
    # living_drawing only: `selected` keeps the Library pick; `album` cycles every full draw cycle.
    photo_playback: PhotoPlaybackMode = "selected"

    # --- WS281x / NeoPixel (SK6812 compatible) — wiring is row-major: idx = y * width + x
    pi_gpio_pin: int = Field(default=18, ge=2, le=40, description="BCM GPIO number for DATA line")
    pi_led_freq_hz: int = Field(default=800_000, ge=400_000, le=1_200_000)
    pi_led_dma: int = Field(default=10, ge=0, le=14)
    pi_led_channel: int = Field(default=0, ge=0, le=1)
    pi_invert_signal: bool = False
    # Strip global brightness (hardware) 0..1; RGB values are still scaled by art brightness upstream.
    pi_strip_brightness: float = Field(default=1.0, ge=0.0, le=1.0)
    # Extra gain after render (useful if panels look dim). 1.0 = no change.
    pi_rgb_gain: float = Field(default=1.0, ge=0.0, le=2.0)
    # Registered name in rpi_ws281x (e.g. WS2812_STRIP, WS2811_STRIP_GRB)
    pi_strip_type: str = Field(default="WS2812_STRIP")


class OpenAIIntegration(BaseModel):
    """Stored locally (e.g. config/settings.yaml). Prefer UI over environment variables."""

    api_key: str = ""
    model: str = ""


class IntegrationsSettings(BaseModel):
    openai: OpenAIIntegration = Field(default_factory=OpenAIIntegration)


class RuntimeSettings(BaseModel):
    matrix: MatrixSettings = MatrixSettings()
    stream: StreamSettings = StreamSettings()
    art: ArtSettings = ArtSettings()
    simulator: SimulatorSettings = SimulatorSettings()
    output: OutputSettings = OutputSettings()
    integrations: IntegrationsSettings = Field(default_factory=IntegrationsSettings)
    running: bool = False
    version: int = 0

