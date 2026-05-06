from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.ai.openai_toolpath import ToolpathParseError, refine_toolpath_with_openai, validate_toolpath
from app.engine.path_stitch import normalize_ai_toolpath
from app.engine.patterns import PATTERN_INFOS
from app.engine.renderer import FrameRenderer
from app.image_library import ImageLibrary
from app.perf_service import PerfService
from app.settings_public import public_settings_dict
from app.settings_service import SettingsService


class RefineToolpathBody(BaseModel):
    model: str | None = None


class ImageLabelBody(BaseModel):
    label: str


def build_routes(
    settings_service: SettingsService,
    perf_service: PerfService,
    image_library: ImageLibrary,
    renderer: FrameRenderer,
) -> APIRouter:
    router = APIRouter(prefix="/api")

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
        return public_settings_dict(settings_service.set_running(False))

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
                    "size_bytes": i.size_bytes,
                    "has_ai_toolpath": image_library.has_toolpath(i.id),
                }
                for i in imgs
            ]
        }

    @router.post("/images/upload")
    async def images_upload(
        file: UploadFile = File(...),
        label: str | None = Form(default=None),
    ) -> dict:
        content = await file.read()
        saved = image_library.save_upload(file.filename or "upload.bin", content, label=label)
        return {"id": saved.id, "filename": saved.filename, "size_bytes": saved.size_bytes, "label": saved.label}

    def _clear_drawing_if_match(image_id: str):
        s = settings_service.get()
        if s.art.drawing_id != image_id:
            return None
        return settings_service.update({"art": {"drawing_id": None}})

    @router.delete("/images/{image_id}/toolpath")
    async def images_delete_toolpath(image_id: str) -> dict:
        if not image_library.get(image_id):
            raise HTTPException(status_code=404, detail="Image not found")
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

    @router.get("/images/{image_id}")
    async def images_get(image_id: str):
        e = image_library.get(image_id)
        if not e:
            return {"error": "not_found"}
        return FileResponse(str(e.path))

    @router.post("/images/{image_id}/refine-toolpath")
    async def images_refine_toolpath(image_id: str, body: RefineToolpathBody | None = None) -> dict:
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
            pts = validate_toolpath(raw, w, h)
            expanded = normalize_ai_toolpath(pts, w, h)
        except ToolpathParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        payload = {
            "version": 1,
            "image_id": image_id,
            "matrix": {"width": w, "height": h},
            "model": model_used,
            "raw": raw,
            "expanded_points": [[int(x), int(y)] for x, y in expanded],
        }
        image_library.save_toolpath(image_id, payload)
        renderer.invalidate_living_drawing(image_id)

        return {"ok": True, "image_id": image_id, "points": len(expanded)}

    return router

