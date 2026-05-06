from __future__ import annotations

import base64
import json
import os
from io import BytesIO
from typing import Any, Dict, List, Tuple

import httpx
from PIL import Image


PixelPoint = Tuple[int, int]

TOOLPATH_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": "LED matrix single-stroke polyline. Only width, height, and path are required for the app.",
    "properties": {
        "width": {"type": "integer", "minimum": 1, "description": "Must equal canvas width W."},
        "height": {"type": "integer", "minimum": 1, "description": "Must equal canvas height H."},
        "path": {
            "type": "array",
            "description": (
                "Ordered pixel centers tracing the REFERENCE photo's subject silhouette and major contours "
                "on this LED grid (same pose, placement, and recognizable outline—not a different drawing)."
            ),
            "minItems": 2,
            "items": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": {"type": "integer"},
            },
        },
    },
    "required": ["width", "height", "path"],
}


class ToolpathParseError(RuntimeError):
    pass


def build_prompt(width: int, height: int) -> str:
    return (
        "You trace ONE CONTINUOUS LINE onto a tiny LED MATRIX from the REFERENCE PHOTO you were shown.\n"
        f"The LED canvas is exactly W={width} pixels wide by H={height} pixels tall.\n"
        "Coordinates are integers with origin at top-left: x increases right, y increases down.\n"
        "\n"
        "LIKELINESS FIRST (non-negotiable):\n"
        "- Your polyline must depict THE SAME subject, pose, facing direction, and rough proportions as the photo.\n"
        "- Someone who knows the photo should immediately say “that’s the same picture,” even though it’s only a line.\n"
        "- Preserve overall silhouette and where major masses sit (head vs body vs object vs limbs).\n"
        "- Map the composition onto the grid faithfully: if the subject sits left-heavy in the photo, it stays left-heavy "
        f"on this {width}×{height} canvas (do NOT invent a centered icon).\n"
        "- Trace recognizable outer contours and a few critical inner edges (hairline, jaw, limb bends, object rims)—"
        "omit fine texture and shading.\n"
        "\n"
        "Hard negatives:\n"
        "- Do NOT draw a different scene, symbol, cartoon mascot, or generic portrait unrelated to the reference.\n"
        "- Do NOT simplify into an unrelated minimal glyph.\n"
        "- Stylization is allowed ONLY inside accurate likeness (economical line, slight exaggeration)—never instead of it.\n"
        "\n"
        "Line character (secondary—Picasso / Matisse spirit):\n"
        "- Confident, flowing ink-like rhythm with playful bends where they still track real anatomy/object edges.\n"
        "- Matisse-like graceful curves and Picasso-like crisp corners where the photo has angles.\n"
        "- Small loops allowed at eyes/corners/hands if they still match the reference landmarks.\n"
        "\n"
        "Technical constraints:\n"
        "- ONE mostly continuous polyline in drawing order (minimal lifts / jumps).\n"
        "- Prefer 8-connected steps between consecutive pixels.\n"
        "- Avoid random scribble-fill; crossings rare and intentional.\n"
        "- Keep coordinates INSIDE [0,W-1] and [0,H-1].\n"
        "\n"
        "Output for our pipeline:\n"
        "- Respond ONLY as structured JSON matching the provided schema: fields width, height, path.\n"
        "- width and height MUST equal W and H above.\n"
        "- path MUST list pixel centers in visit order along that silhouette/contour walk.\n"
        "- Prefer ~80–400 points (more if needed on larger canvases) so contours aren’t overly coarse.\n"
    )


def open_rgb_bytes(image_bytes: bytes) -> Image.Image:
    return Image.open(BytesIO(image_bytes)).convert("RGB")


def refine_toolpath_with_openai(
    *,
    image_bytes: bytes,
    width: int,
    height: int,
    api_key: str | None = None,
    model: str | None = None,
) -> tuple[Dict[str, Any], str]:
    api_key_resolved = (api_key or "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key_resolved:
        raise RuntimeError(
            "OpenAI API key missing: add it under Settings → Integrations → OpenAI, "
            "or set OPENAI_API_KEY in the environment / `.env`."
        )

    chosen_model = (model or "").strip() or os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()

    img = open_rgb_bytes(image_bytes)

    # Larger preview + high vision detail so the model can actually match the photo (was overly compressed).
    preview_max_side = int(os.getenv("OPENAI_PREVIEW_MAX_SIDE", "1536"))
    img.thumbnail((preview_max_side, preview_max_side))

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"

    image_detail = (os.getenv("OPENAI_IMAGE_DETAIL", "high") or "high").strip().lower()
    if image_detail not in ("low", "high", "auto", "original"):
        image_detail = "high"

    verbosity = (os.getenv("OPENAI_VERBOSITY", "medium") or "medium").strip().lower()
    if verbosity not in ("low", "medium", "high"):
        verbosity = "medium"

    payload = {
        "model": chosen_model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "REFERENCE PHOTO is next. Every vertex you output must trace THIS exact photo's subject "
                            "and layout—not an invented drawing."
                        ),
                    },
                    {
                        "type": "input_image",
                        "detail": image_detail,
                        "image_url": data_url,
                    },
                    {"type": "input_text", "text": build_prompt(width, height)},
                ],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "led_toolpath_v1",
                "strict": True,
                "schema": TOOLPATH_RESPONSE_SCHEMA,
            },
            "verbosity": verbosity,
        },
    }

    headers = {"Authorization": f"Bearer {api_key_resolved}", "Content-Type": "application/json"}

    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
        r = client.post("https://api.openai.com/v1/responses", headers=headers, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"OpenAI error {r.status_code}: {r.text[:2000]}")
        data = r.json()

    text_out = _extract_output_text(data)
    parsed = _parse_strict_json(text_out)
    return parsed, chosen_model


def _extract_output_text(resp_json: Dict[str, Any]) -> str:
    output = resp_json.get("output")
    if not isinstance(output, list):
        raise RuntimeError(f"Unexpected OpenAI response shape: keys={list(resp_json.keys())}")

    chunks: List[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("output_text", "text"):
                t = block.get("text")
                if isinstance(t, str) and t:
                    chunks.append(t)

    text = "".join(chunks).strip()
    if text:
        return text
    raise RuntimeError(f"Unexpected OpenAI response shape: keys={list(resp_json.keys())}")


def _parse_strict_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    # If model accidentally wraps JSON, strip common fences
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ToolpathParseError(f"Model returned non-JSON: {e}; snippet={text[:300]}") from e
    if not isinstance(obj, dict):
        raise ToolpathParseError("JSON root must be an object")
    return obj


def validate_toolpath(obj: Dict[str, Any], expected_w: int, expected_h: int) -> List[PixelPoint]:
    w = int(obj.get("width"))
    h = int(obj.get("height"))
    path = obj.get("path")
    if w != expected_w or h != expected_h:
        raise ToolpathParseError(f"width/height mismatch: got {w}x{h}, expected {expected_w}x{expected_h}")
    if not isinstance(path, list) or len(path) < 2:
        raise ToolpathParseError("path must be a non-empty list of points")

    out: List[PixelPoint] = []
    for pt in path:
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise ToolpathParseError(f"bad point: {pt}")
        x = int(pt[0])
        y = int(pt[1])
        if x < 0 or x >= w or y < 0 or y >= h:
            raise ToolpathParseError(f"point out of bounds: {(x,y)} for {w}x{h}")
        out.append((x, y))
    return out
