from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import build_routes
from app.api.websocket import build_websocket
from app.engine.renderer import FrameRenderer
from app.image_library import ImageLibrary
from app.perf_service import PerfService
from app.settings_service import SettingsService


def create_app() -> FastAPI:
    load_dotenv()  # optional .env; MVP doesn't require secrets

    here = Path(__file__).resolve()
    backend_dir = here.parents[1]  # .../backend
    project_root = backend_dir.parent  # .../artmaker
    settings_path = project_root / "config" / "settings.yaml"
    perf_path = project_root / "config" / "perf.yaml"
    frontend_dir = project_root / "frontend"

    settings_service = SettingsService(settings_path=settings_path)
    perf_service = PerfService(perf_path=perf_path)
    image_library = ImageLibrary(root_dir=project_root)
    renderer = FrameRenderer(image_library=image_library)

    app = FastAPI(title="AI Light Canvas", version="0.1.0")

    app.include_router(build_routes(settings_service, perf_service, image_library, renderer))
    app.include_router(build_websocket(settings_service, perf_service, renderer, image_library))

    # Serve the frontend at /
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app


app = create_app()

