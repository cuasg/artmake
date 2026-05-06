from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class Frame(BaseModel):
    width: int = Field(ge=1, le=512)
    height: int = Field(ge=1, le=512)
    # pixels[y][x] = [r,g,b] where each is 0..255
    pixels: List[List[List[int]]]

