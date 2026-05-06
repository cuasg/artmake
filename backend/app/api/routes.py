from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

import os
import asyncio
import uuid
from dataclasses import dataclass

from app.ai.openai_toolpath import OpenAIRequestError, ToolpathParseError, refine_toolpath_with_openai, validate_strokes, validate_toolpath
from app.ai.openai_image_stylize import OpenAIImageError, stylize_photo_to_lineart_png
from app.engine.line_draw import order_points_nearest
from app.engine.path_stitch import normalize_ai_strokes, normalize_ai_toolpath
from app.engine.patterns import PATTERN_INFOS
from app.engine.renderer import FrameRenderer
from app.engine.line_art_vectorize import image_to_strokes_lineart
from PIL import Image
from app.image_library import ImageLibrary
from app.perf_service import PerfService
from app.settings_public import public_settings_dict
from app.settings_service import SettingsService


class RefineToolpathBody(BaseModel):
    model: str | None = None


class StylizeImageBody(BaseModel):
    model: str | None = None


class ImageLabelBody(BaseModel):
    label: str


class CropFocusBody(BaseModel):
    crop_focus: str


class ToolpathKeyBody(BaseModel):
    w: int
    h: int
    source: str  # ai|vectorized


def build_routes(
    settings_service: SettingsService,
    perf_service: PerfService,
    image_library: ImageLibrary,
    renderer: FrameRenderer,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    DEFAULT_PRESETS: list[tuple[int, int]] = [
        (8, 8),
        (16, 16),
        (32, 32),
        (64, 64),
        (128, 128),
        (64, 96),
    ]

    @dataclass
    class JobState:
        id: str
        kind: str
        image_id: str
        total: int
        done: int = 0
        status: str = "running"  # running|done|error
        status_label: str = "Working"
        current: str | None = None
        error: str | None = None

    jobs: dict[str, JobState] = {}

    @router.get("/jobs/{job_id}")
    async def jobs_get(job_id: str) -> dict:
        j = jobs.get(job_id)
        if not j:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "id": j.id,
            "kind": j.kind,
            "image_id": j.image_id,
            "total": int(j.total),
            "done": int(j.done),
            "status": j.status,
            "status_label": j.status_label,
            "current": j.current,
            "error": j.error,
        }

    def _save_local_toolpath(image_id: str, img_path, w: int, h: int, source: str) -> int:
        """
        Generate and persist a local toolpath variant.
        Returns number of strokes saved.
        """
        def crop_to_aspect(img: Image.Image, tw: int, th: int, focus: str) -> Image.Image:
            # Center-crop (or edge-crop) to preserve aspect ratio before resizing.
            tw = int(tw)
            th = int(th)
            if tw <= 0 or th <= 0:
                return img
            fw = float(tw) / float(th)
            iw, ih = img.size
            if iw <= 0 or ih <= 0:
                return img
            fi = float(iw) / float(ih)
            if abs(fi - fw) < 1e-6:
                return img
            # crop width or height
            if fi > fw:
                # too wide: crop width
                new_w = int(round(ih * fw))
                if focus == "left":
                    x0 = 0
                elif focus == "right":
                    x0 = iw - new_w
                else:
                    x0 = (iw - new_w) // 2
                box = (max(0, x0), 0, min(iw, x0 + new_w), ih)
            else:
                # too tall: crop height
                new_h = int(round(iw / fw))
                if focus == "top":
                    y0 = 0
                elif focus == "bottom":
                    y0 = ih - new_h
                else:
                    y0 = (ih - new_h) // 2
                box = (0, max(0, y0), iw, min(ih, y0 + new_h))
            return img.crop(box)

        focus = "center"
        try:
            e = image_library.get(image_id)
            if e:
                focus = (e.crop_focus or "center").strip().lower()
        except Exception:
            focus = "center"

        if source != "vectorized":
            raise ValueError("source must be vectorized")
        img = Image.open(img_path).convert("RGB")
        img = crop_to_aspect(img, int(w), int(h), focus)
        strokes = image_to_strokes_lineart(img, int(w), int(h))
        expanded_strokes = normalize_ai_strokes(strokes, int(w), int(h))

        payload = {
            "version": 2,
            "image_id": image_id,
            "matrix": {"width": int(w), "height": int(h)},
            "model": None,
            "raw": {"width": int(w), "height": int(h), "strokes": [{"points": s} for s in expanded_strokes]},
            "expanded_strokes": [[[int(x), int(y)] for x, y in stroke] for stroke in expanded_strokes],
            "expanded_points": None,
            "source": source,
        }
        image_library.save_toolpath(image_id, payload, w=int(w), h=int(h), source=source)
        return len(expanded_strokes)

    @router.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @router.get("/patterns")
    async def patterns() -> dict:
        return {
            "patterns": [
                {
                    "name": p.name,
                    "display_name": p.display_name,
                    "description": p.description,
                }
                for p in PATTERN_INFOS
            ]
        }

    @router.get("/settings")
    async def get_settings() -> dict:
        return public_settings_dict(settings_service.get())

    @router.post("/settings")
    async def patch_settings(patch: dict) -> dict:
        updated = settings_service.update(patch)
        return public_settings_dict(updated)

    @router.post("/control/start")
    async def start() -> dict:
        # Running state is ephemeral; don't persist it.
        return public_settings_dict(settings_service.set_running(True))

    @router.post("/control/stop")
    async def stop() -> dict:
        # Running state is ephemeral; don't persist it.
        updated = settings_service.set_running(False)
        # “Stop” should behave like a hard stop: clear renderer state so the next
        # start uses the latest settings (matrix size, pattern, drawing state) from frame 0.
        try:
            renderer.reset()
        except Exception:
            pass
        return public_settings_dict(updated)

    @router.post("/control/reset")
    async def reset() -> dict:
        return public_settings_dict(settings_service.reset())

    @router.get("/perf")
    async def perf() -> dict:
        return perf_service.dump()

    @router.post("/perf/reset")
    async def perf_reset() -> dict:
        s = settings_service.get()
        perf_service.reset_profile(s.matrix.width, s.matrix.height, s.art.pattern)
        return {"ok": True}

    @router.get("/images")
    async def images_list() -> dict:
        imgs = image_library.list()
        return {
            "images": [
                {
                    "id": i.id,
                    "filename": i.filename,
                    "label": i.label,
                    "crop_focus": getattr(i, "crop_focus", "center"),
                    "parent_id": getattr(i, "parent_id", None),
                    "kind": getattr(i, "kind", "original"),
                    "size_bytes": i.size_bytes,
                    "has_ai_toolpath": image_library.has_toolpath(i.id),
                    "toolpaths": image_library.list_toolpaths(i.id),
                }
                for i in imgs
            ]
        }

    @router.post("/images/upload")
    async def images_upload(
        file: UploadFile = File(...),
        label: str | None = Form(default=None),
        crop_focus: str | None = Form(default=None),
    ) -> dict:
        content = await file.read()
        saved = image_library.save_upload(file.filename or "upload.bin", content, label=label, crop_focus=crop_focus)
        # Kick off local preset generation asynchronously so UI doesn't block.
        job_id = uuid.uuid4().hex
        job = JobState(
            id=job_id,
            kind="upload_presets",
            image_id=saved.id,
            total=len(DEFAULT_PRESETS),
            status="running",
            status_label="Generating vectorized presets",
        )
        jobs[job_id] = job

        async def _run() -> None:
            try:
                for (w, h) in DEFAULT_PRESETS:
                    job.current = f"{w}×{h}"
                    source = "vectorized"
                    if image_library.load_toolpath(saved.id, w, h, source):
                        job.done += 1
                        continue
                    # CPU-heavy: run in thread so we don't stall the event loop.
                    await asyncio.to_thread(_save_local_toolpath, saved.id, saved.path, w, h, source)
                    job.done += 1
            except Exception as e:
                job.status = "error"
                job.error = str(e)
                job.status_label = "Failed"
                return
            job.status = "done"
            job.status_label = "Done"
            job.current = None
            renderer.invalidate_living_drawing(saved.id)

        asyncio.create_task(_run())

        return {
            "id": saved.id,
            "filename": saved.filename,
            "size_bytes": saved.size_bytes,
            "label": saved.label,
            "job_id": job_id,
        }

    def _clear_drawing_if_match(image_id: str):
        s = settings_service.get()
        if s.art.drawing_id != image_id:
            return None
        return settings_service.update({"art": {"drawing_id": None}})

    @router.get("/images/{image_id}/toolpaths/{w}x{h}")
    async def images_get_toolpath(image_id: str, w: int, h: int) -> dict:
        if not image_library.get(image_id):
            raise HTTPException(status_code=404, detail="Image not found")
        # default to ai variant
        j = image_library.load_toolpath(image_id, w, h, "ai")
        if not j:
            raise HTTPException(status_code=404, detail="Toolpath not found")
        return j

    @router.get("/images/{image_id}/toolpaths/{w}x{h}/{source}")
    async def images_get_toolpath_variant(image_id: str, w: int, h: int, source: str) -> dict:
        if not image_library.get(image_id):
            raise HTTPException(status_code=404, detail="Image not found")
        j = image_library.load_toolpath(image_id, w, h, source)
        if not j:
            raise HTTPException(status_code=404, detail="Toolpath not found")
        return j

    @router.delete("/images/{image_id}/toolpaths/{w}x{h}")
    async def images_delete_toolpath_variant(image_id: str, w: int, h: int) -> dict:
        if not image_library.get(image_id):
            raise HTTPException(status_code=404, detail="Image not found")
        removed = image_library.delete_toolpath(image_id, w, h, "ai")
        renderer.invalidate_living_drawing(image_id)
        return {"ok": True, "removed": removed}

    @router.delete("/images/{image_id}/toolpaths/{w}x{h}/{source}")
    async def images_delete_toolpath_variant_source(image_id: str, w: int, h: int, source: str) -> dict:
        if not image_library.get(image_id):
            raise HTTPException(status_code=404, detail="Image not found")
        removed = image_library.delete_toolpath(image_id, w, h, source)
        renderer.invalidate_living_drawing(image_id)
        return {"ok": True, "removed": removed}

    @router.post("/images/{image_id}/toolpaths/{w}x{h}/{source}/generate")
    async def images_generate_toolpath(image_id: str, w: int, h: int, source: str) -> dict:
        e = image_library.get(image_id)
        if not e:
            raise HTTPException(status_code=404, detail="Image not found")
        source = source.strip().lower()
        if source not in ("vectorized",):
            raise HTTPException(status_code=400, detail="source must be vectorized")

        strokes_n = _save_local_toolpath(image_id, e.path, int(w), int(h), "vectorized")
        renderer.invalidate_living_drawing(image_id)

        return {"ok": True, "image_id": image_id, "w": int(w), "h": int(h), "source": source, "strokes": int(strokes_n)}

    @router.delete("/images/{image_id}/toolpath")
    async def images_delete_toolpath(image_id: str) -> dict:
        if not image_library.get(image_id):
            raise HTTPException(status_code=404, detail="Image not found")
        # remove all toolpaths for this image
        removed = image_library.delete_toolpath(image_id)
        renderer.invalidate_living_drawing(image_id)
        return {"ok": True, "removed": removed}

    @router.delete("/images/{image_id}")
    async def images_delete(image_id: str) -> dict:
        try:
            image_library.delete_image(image_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Image not found") from None
        renderer.invalidate_living_drawing(image_id)
        updated = _clear_drawing_if_match(image_id)
        out: dict = {"ok": True}
        if updated is not None:
            out["settings"] = public_settings_dict(updated)
        return out

    @router.patch("/images/{image_id}")
    async def images_rename(image_id: str, body: ImageLabelBody) -> dict:
        lab = (body.label or "").strip()
        if not lab:
            raise HTTPException(status_code=422, detail="label must be non-empty")
        try:
            image_library.set_label(image_id, lab)
        except ValueError:
            raise HTTPException(status_code=404, detail="Image not found") from None
        return {"ok": True, "id": image_id, "label": lab}

    @router.patch("/images/{image_id}/crop-focus")
    async def images_set_crop_focus(image_id: str, body: CropFocusBody) -> dict:
        try:
            v = image_library.set_crop_focus(image_id, body.crop_focus)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "id": image_id, "crop_focus": v}

    @router.get("/images/{image_id}")
    async def images_get(image_id: str):
        e = image_library.get(image_id)
        if not e:
            return {"error": "not_found"}
        return FileResponse(str(e.path))

    @router.post("/images/{image_id}/refine-toolpath")
    async def images_refine_toolpath(image_id: str, body: RefineToolpathBody | None = None) -> dict:
        try:
            e = image_library.get(image_id)
            if not e:
                raise HTTPException(status_code=404, detail="Image not found")

            settings = settings_service.get()
            w = settings.matrix.width
            h = settings.matrix.height

            img_bytes = e.path.read_bytes()
            oa = settings.integrations.openai
            override_model = (body.model.strip() if body and body.model else "") or ""
            settings_model = (oa.model or "").strip()
            model_arg = override_model or settings_model or None

            try:
                raw, model_used = refine_toolpath_with_openai(
                    image_bytes=img_bytes,
                    width=w,
                    height=h,
                    api_key=(oa.api_key or "").strip() or None,
                    model=model_arg,
                )
                expanded_strokes = None
                expanded = None
                if isinstance(raw, dict) and isinstance(raw.get("strokes"), list):
                    strokes = validate_strokes(raw, w, h)
                    expanded_strokes = normalize_ai_strokes(strokes, w, h)
                else:
                    pts = validate_toolpath(raw, w, h)
                    expanded = normalize_ai_toolpath(pts, w, h)
            except ToolpathParseError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except OpenAIRequestError as exc:
                # Map auth/rate-limit/etc to the correct HTTP status for the UI.
                raise HTTPException(status_code=int(exc.status_code), detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            payload = {
                "version": 2,
                "image_id": image_id,
                "matrix": {"width": w, "height": h},
                "model": model_used,
                "raw": raw,
                "expanded_strokes": (
                    [[[int(x), int(y)] for x, y in stroke] for stroke in expanded_strokes]
                    if expanded_strokes is not None
                    else None
                ),
                "expanded_points": (
                    [[int(x), int(y)] for x, y in expanded] if expanded_strokes is None and expanded is not None else None
                ),
            }
            image_library.save_toolpath(image_id, payload, w=w, h=h, source="ai")
            renderer.invalidate_living_drawing(image_id)

            total_points = (
                sum(len(s) for s in expanded_strokes) if expanded_strokes is not None else len(expanded or [])
            )
            return {
                "ok": True,
                "image_id": image_id,
                "strokes": (len(expanded_strokes) if expanded_strokes is not None else 1),
                "points": int(total_points),
            }
        except HTTPException:
            raise
        except Exception as exc:
            # Surface a useful message during local development.
            raise HTTPException(status_code=500, detail=f"Internal error: {type(exc).__name__}: {exc}") from exc

    @router.post("/images/{image_id}/toolpaths/{w}x{h}/ai/generate")
    async def images_refine_toolpath_for_size(image_id: str, w: int, h: int, body: RefineToolpathBody | None = None) -> dict:
        """
        Gallery-friendly variant: generate an AI toolpath for a specific matrix size.
        """
        try:
            e = image_library.get(image_id)
            if not e:
                raise HTTPException(status_code=404, detail="Image not found")

            img_bytes = e.path.read_bytes()
            settings = settings_service.get()
            oa = settings.integrations.openai
            override_model = (body.model.strip() if body and body.model else "") or ""
            settings_model = (oa.model or "").strip()
            model_arg = override_model or settings_model or None

            try:
                raw, model_used = refine_toolpath_with_openai(
                    image_bytes=img_bytes,
                    width=int(w),
                    height=int(h),
                    api_key=(oa.api_key or "").strip() or None,
                    model=model_arg,
                )
                expanded_strokes = None
                expanded = None
                if isinstance(raw, dict) and isinstance(raw.get("strokes"), list):
                    strokes = validate_strokes(raw, int(w), int(h))
                    expanded_strokes = normalize_ai_strokes(strokes, int(w), int(h))
                else:
                    pts = validate_toolpath(raw, int(w), int(h))
                    expanded = normalize_ai_toolpath(pts, int(w), int(h))
            except ToolpathParseError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except OpenAIRequestError as exc:
                raise HTTPException(status_code=int(exc.status_code), detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            payload = {
                "version": 2,
                "image_id": image_id,
                "matrix": {"width": int(w), "height": int(h)},
                "model": model_used,
                "raw": raw,
                "expanded_strokes": (
                    [[[int(x), int(y)] for x, y in stroke] for stroke in expanded_strokes]
                    if expanded_strokes is not None
                    else None
                ),
                "expanded_points": (
                    [[int(x), int(y)] for x, y in expanded] if expanded_strokes is None and expanded is not None else None
                ),
                "source": "ai",
            }
            image_library.save_toolpath(image_id, payload, w=int(w), h=int(h), source="ai")
            renderer.invalidate_living_drawing(image_id)

            total_points = (
                sum(len(s) for s in expanded_strokes) if expanded_strokes is not None else len(expanded or [])
            )
            return {
                "ok": True,
                "image_id": image_id,
                "w": int(w),
                "h": int(h),
                "source": "ai",
                "strokes": (len(expanded_strokes) if expanded_strokes is not None else 1),
                "points": int(total_points),
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Internal error: {type(exc).__name__}: {exc}") from exc

    @router.post("/images/{image_id}/ai-stylize")
    async def images_ai_stylize(image_id: str, body: StylizeImageBody | None = None) -> dict:
        """
        Generate a black-on-white line-art PNG from the original photo using an image-capable model.
        The derived image is saved into the gallery tied to the original (parent_id) and can then be vectorized locally.
        """
        try:
            e = image_library.get(image_id)
            if not e:
                raise HTTPException(status_code=404, detail="Image not found")

            settings = settings_service.get()
            oa = settings.integrations.openai
            override_model = (body.model.strip() if body and body.model else "") or ""
            settings_model = (oa.model or "").strip()
            # For image stylization, allow a dedicated env var; fall back to settings model if user set it.
            model_arg = override_model or os.getenv("OPENAI_IMAGE_MODEL", "").strip() or settings_model or None

            try:
                out_png, meta = stylize_photo_to_lineart_png(
                    image_bytes=e.path.read_bytes(),
                    api_key=(oa.api_key or "").strip() or None,
                    model=model_arg,
                )
            except OpenAIImageError as exc:
                raise HTTPException(status_code=int(exc.status_code), detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            # Save as a derived image entry.
            derived_label = f"{e.label} · AI line art"
            derived = image_library.save_upload(
                original_filename=f"{image_id}__ai_lineart.png",
                content=out_png,
                label=derived_label,
                crop_focus=e.crop_focus,
                parent_id=image_id,
                kind="ai_lineart",
            )

            # Auto-generate local variants for the derived image immediately.
            generated: list[dict] = []
            for (w2, h2) in DEFAULT_PRESETS:
                source = "vectorized"
                if image_library.load_toolpath(derived.id, w2, h2, source):
                    continue
                try:
                    strokes_n = _save_local_toolpath(derived.id, derived.path, w2, h2, source)
                    generated.append({"w": int(w2), "h": int(h2), "source": source, "strokes": int(strokes_n)})
                except Exception:
                    pass

            return {
                "ok": True,
                "parent_id": image_id,
                "derived_id": derived.id,
                "derived_filename": derived.filename,
                "generated": generated,
                "meta": meta,
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Internal error: {type(exc).__name__}: {exc}") from exc

    return router

