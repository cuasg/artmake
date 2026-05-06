from __future__ import annotations

import base64
import os
import re
from io import BytesIO
from typing import Any, Dict, Tuple

import httpx
from PIL import Image


class OpenAIImageError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


def _sanitize_api_key(api_key: str | None) -> str:
    api_key_resolved = (api_key or "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    api_key_resolved = api_key_resolved.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    api_key_resolved = re.sub(r"\s+", "", api_key_resolved)
    api_key_resolved = api_key_resolved.encode("ascii", "ignore").decode("ascii")
    return api_key_resolved


def _data_url_png(image_bytes: bytes, *, max_side: int = 1536) -> str:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((int(max_side), int(max_side)))
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def stylize_photo_to_lineart_png(
    *,
    image_bytes: bytes,
    api_key: str | None = None,
    model: str | None = None,
    input_fidelity: str | None = None,
) -> Tuple[bytes, Dict[str, Any]]:
    """
    Uses OpenAI Images Edits API to produce a black-on-white line-art PNG
    from a photo, suitable for downstream vectorization.
    """
    key = _sanitize_api_key(api_key)
    if not key:
        raise RuntimeError(
            "OpenAI API key missing: add it under Settings → ChatGPT / OpenAI, "
            "or set OPENAI_API_KEY in the environment / `.env`."
        )

    chosen_model = (model or "").strip() or os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5").strip()
    fidelity = (input_fidelity or "").strip().lower() or os.getenv("OPENAI_IMAGE_INPUT_FIDELITY", "high").strip().lower()
    if fidelity not in ("high", "low"):
        fidelity = "high"

    # Prefer 2:3 portrait output (matches 64x96). We can center-crop to square later.
    size = (os.getenv("OPENAI_IMAGE_SIZE", "1024x1536") or "1024x1536").strip()
    if size not in ("1024x1024", "1536x1024", "1024x1536", "auto"):
        size = "1024x1536"

    prompt = (
        "Transform the input photograph into a clean, high-contrast black-ink-on-white line drawing.\n"
        "Constraints:\n"
        "- Output must be ONLY black lines on a pure white background.\n"
        "- No shading, no gray, no colors, no halftone, no texture.\n"
        "- Preserve the essence/likeness: same subject, pose, proportions, and major contours.\n"
        "- Simplify details aggressively so it still reads on a low-resolution LED matrix.\n"
        "- Use confident, continuous contour lines (Picasso/Matisse-inspired), but never at the expense of likeness.\n"
        "- Prefer a few strong interior feature lines only when essential.\n"
        "- Keep background minimal or empty.\n"
        "- Composition: keep the subject centered with comfortable margins so a center square crop still contains the subject.\n"
    )

    img_url = _data_url_png(image_bytes, max_side=int(os.getenv("OPENAI_PREVIEW_MAX_SIDE", "1536")))

    payload: Dict[str, Any] = {
        "model": chosen_model,
        "images": [{"image_url": img_url}],
        "prompt": prompt,
        "input_fidelity": fidelity,
        "n": 1,
        "output_format": "png",
        "quality": (os.getenv("OPENAI_IMAGE_QUALITY", "high") or "high").strip().lower(),
        "size": size,
        "moderation": (os.getenv("OPENAI_IMAGE_MODERATION", "auto") or "auto").strip().lower(),
    }

    headers = {"Authorization": f"Bearer {key}"}
    timeout_s = float(os.getenv("OPENAI_TIMEOUT_S", "240"))
    connect_s = float(os.getenv("OPENAI_CONNECT_TIMEOUT_S", "20"))

    with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=connect_s)) as client:
        r = client.post("https://api.openai.com/v1/images/edits", headers=headers, json=payload)

    if r.status_code >= 400:
        try:
            j = r.json()
            msg = j.get("error", {}).get("message") if isinstance(j, dict) else None
        except Exception:
            msg = None
        raise OpenAIImageError(r.status_code, msg or f"OpenAI image error {r.status_code}")

    j = r.json()
    data = j.get("data") if isinstance(j, dict) else None
    if not isinstance(data, list) or not data:
        raise OpenAIImageError(500, "OpenAI image response missing data[0].b64_json")
    b64 = data[0].get("b64_json") if isinstance(data[0], dict) else None
    if not isinstance(b64, str) or not b64.strip():
        raise OpenAIImageError(500, "OpenAI image response missing b64_json")
    out_bytes = base64.b64decode(b64)

    meta = {
        "model": chosen_model,
        "size": j.get("size"),
        "quality": j.get("quality"),
        "input_fidelity": fidelity,
    }
    return out_bytes, meta

