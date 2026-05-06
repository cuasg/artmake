from __future__ import annotations

import base64
import json
import os
import re
from io import BytesIO
from typing import Any, Dict, List, Tuple

import httpx
from PIL import Image
from PIL import ImageFilter, ImageOps


PixelPoint = Tuple[int, int]

TOOLPATH_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": "LED matrix single-stroke polyline. Only width, height, and path are required for the app.",
    "properties": {
        "width": {"type": "integer", "minimum": 1, "description": "Must equal canvas width W."},
        "height": {"type": "integer", "minimum": 1, "description": "Must equal canvas height H."},
        "strokes": {
            "type": "array",
            "description": (
                "Ordered brush strokes. Each stroke is a polyline of pixel coordinates in drawing order. "
                "Use multiple strokes to simulate an artist building the drawing."
            ),
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "points": {
                        "type": "array",
                        "minItems": 2,
                        "items": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 2,
                            "items": {"type": "integer"},
                        },
                    }
                },
                "required": ["points"],
            },
        },
    },
    # NOTE: For OpenAI Structured Outputs (strict), required must include all fields we expect.
    # We require strokes (not path) so the model always returns brush-stroke segments.
    "required": ["width", "height", "strokes"],
}


class ToolpathParseError(RuntimeError):
    pass


class OpenAIRequestError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


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
        "- Respond ONLY as structured JSON matching the provided schema.\n"
        "- width and height MUST equal W and H above.\n"
        "- Prefer strokes[] (multiple brush strokes). Each stroke has points[] in drawing order.\n"
        "- If you cannot produce multiple strokes, you may fall back to a single path.\n"
        "- Total points should scale with canvas: on 16×16 aim ~80–250 total points; on 64×64 aim ~800–3000.\n"
        "\n"
        "CRITICAL PLACEMENT AID:\n"
        "- You will also be given a GRID PREVIEW image that is already mapped to this W×H canvas (upscaled for viewing).\n"
        "- Use the GRID PREVIEW to decide where pixels go. Do not guess layout from the high-res photo alone.\n"
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
    # Clipboard pastes can include invisible Unicode (e.g. zero-width spaces) which breaks header encoding.
    # IMPORTANT: Do NOT remove valid key punctuation like '-' or '_' (OpenAI keys often include them).
    api_key_resolved = api_key_resolved.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "").replace("\u200d", "")
    api_key_resolved = re.sub(r"\s+", "", api_key_resolved)
    # Ensure header-safe ASCII without altering normal key characters like '-'/'_'.
    api_key_resolved = api_key_resolved.encode("ascii", "ignore").decode("ascii")
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

    # Provide a pixel-accurate “grid preview” to anchor the model to exact W×H placement.
    grid = img.resize((int(width), int(height)), Image.BILINEAR)
    grid_up = grid.resize((int(width) * 32, int(height) * 32), Image.NEAREST)

    edges = ImageOps.autocontrast(grid.convert("L").filter(ImageFilter.FIND_EDGES))
    edges_up = edges.resize((int(width) * 32, int(height) * 32), Image.NEAREST).convert("RGB")

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    data_url = f"data:image/png;base64,{b64}"

    buf2 = BytesIO()
    grid_up.save(buf2, format="PNG", optimize=True)
    b64_grid = base64.b64encode(buf2.getvalue()).decode("ascii")
    grid_url = f"data:image/png;base64,{b64_grid}"

    buf3 = BytesIO()
    edges_up.save(buf3, format="PNG", optimize=True)
    b64_edges = base64.b64encode(buf3.getvalue()).decode("ascii")
    edges_url = f"data:image/png;base64,{b64_edges}"

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
                            "You will receive (1) the original REFERENCE PHOTO, (2) a GRID PREVIEW mapped to the LED canvas, "
                            "and (3) an EDGE PREVIEW. Use GRID/EDGE previews to place coordinates accurately."
                        ),
                    },
                    {
                        "type": "input_image",
                        "detail": image_detail,
                        "image_url": data_url,
                    },
                    {"type": "input_text", "text": "GRID PREVIEW (already mapped to W×H canvas, upscaled with nearest-neighbor):"},
                    {"type": "input_image", "detail": "low", "image_url": grid_url},
                    {"type": "input_text", "text": "EDGE PREVIEW (same mapping; use for contour placement):"},
                    {"type": "input_image", "detail": "low", "image_url": edges_url},
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

    timeout_s = float(os.getenv("OPENAI_TIMEOUT_S", "240"))
    connect_s = float(os.getenv("OPENAI_CONNECT_TIMEOUT_S", "20"))

    def _post_once(client: httpx.Client) -> httpx.Response:
        return client.post("https://api.openai.com/v1/responses", headers=headers, json=payload)

    with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=connect_s)) as client:
        try:
            r = _post_once(client)
        except httpx.ReadTimeout as exc:
            # One retry: transient stalls happen; keep UI simple.
            try:
                r = _post_once(client)
            except httpx.ReadTimeout as exc2:
                raise OpenAIRequestError(504, "OpenAI request timed out. Try again.") from exc2
            except httpx.HTTPError as exc2:
                raise OpenAIRequestError(502, f"OpenAI request failed: {type(exc2).__name__}") from exc2
        except httpx.HTTPError as exc:
            raise OpenAIRequestError(502, f"OpenAI request failed: {type(exc).__name__}") from exc

        if r.status_code >= 400:
            # Avoid spewing giant masked-key strings into logs/UI.
            try:
                j = r.json()
                msg = j.get("error", {}).get("message") if isinstance(j, dict) else None
                if isinstance(msg, str) and msg:
                    raise OpenAIRequestError(r.status_code, msg)
            except OpenAIRequestError:
                raise
            except Exception:
                pass
            raise OpenAIRequestError(r.status_code, f"OpenAI error {r.status_code}")
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


def validate_strokes(obj: Dict[str, Any], expected_w: int, expected_h: int) -> List[List[PixelPoint]]:
    w = int(obj.get("width"))
    h = int(obj.get("height"))
    if w != expected_w or h != expected_h:
        raise ToolpathParseError(f"width/height mismatch: got {w}x{h}, expected {expected_w}x{expected_h}")
    strokes = obj.get("strokes")
    if not isinstance(strokes, list) or not strokes:
        raise ToolpathParseError("strokes must be a non-empty array")

    out: List[List[PixelPoint]] = []
    for s in strokes:
        if not isinstance(s, dict):
            raise ToolpathParseError("stroke must be an object")
        pts = s.get("points")
        if not isinstance(pts, list) or len(pts) < 2:
            raise ToolpathParseError("stroke.points must be a list of points")
        stroke_pts: List[PixelPoint] = []
        for pt in pts:
            if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                raise ToolpathParseError(f"bad point: {pt}")
            x = int(pt[0])
            y = int(pt[1])
            if x < 0 or x >= w or y < 0 or y >= h:
                raise ToolpathParseError(f"point out of bounds: {(x, y)} for {w}x{h}")
            stroke_pts.append((x, y))
        out.append(stroke_pts)
    return out
