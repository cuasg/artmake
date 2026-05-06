from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


MatrixPreset = Literal["16x16", "32x32", "64x64", "64x128", "128x64", "128x128"]
PatternName = Literal["waves", "pulse", "fractal_julia", "living_drawing"]
LedShape = Literal["circle", "square"]
OutputMode = Literal["simulator"]


class MatrixSettings(BaseModel):
    width: int = Field(default=64, ge=1, le=512)
    height: int = Field(default=64, ge=1, le=512)
    preset: Optional[MatrixPreset] = None

    @model_validator(mode="after")
    def _apply_preset(self) -> "MatrixSettings":
        if not self.preset:
            return self
        w, h = self.preset.split("x", 1)
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
    toolpath_source: Literal["auto", "ai", "vectorized", "edge"] = "auto"


class SimulatorSettings(BaseModel):
    led_shape: LedShape = "circle"
    led_spacing: int = Field(default=1, ge=0, le=10)
    glow: float = Field(default=0.25, ge=0.0, le=1.0)


class OutputSettings(BaseModel):
    mode: OutputMode = "simulator"


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

