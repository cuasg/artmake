from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.engine.renderer import FrameRenderer
from app.perf_service import PerfService
from app.settings_service import SettingsService


def build_websocket(settings_service: SettingsService, perf_service: PerfService, renderer: FrameRenderer) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        seq = 0
        last_status_sent = 0.0
        last_settings_version = -1
        ema_frame_s: float | None = None
        effective_fps: float = 0.0
        last_learned_sent: int | None = None
        learn_last_update_at = 0.0

        try:
            while True:
                settings = settings_service.get()
                profile_key = f"{settings.matrix.width}x{settings.matrix.height}/{settings.art.pattern}"

                learned_max = perf_service.get_learned_max_fps(
                    settings.matrix.width, settings.matrix.height, settings.art.pattern
                )
                if settings.stream.auto_learn and learned_max:
                    # Apply learned cap at runtime (without persisting to settings.yaml).
                    settings.stream.max_fps = int(learned_max)

                # Send settings only when they change (keeps frames lighter + reduces CPU).
                if settings.version != last_settings_version:
                    await websocket.send_json(
                        {
                            "type": "settings",
                            "ts": time.time(),
                            "settings": {
                                "matrix": settings.matrix.model_dump(),
                                "art": settings.art.model_dump(),
                                "stream": settings.stream.model_dump(),
                                "simulator": settings.simulator.model_dump(),
                                "output": settings.output.model_dump(),
                                "running": settings.running,
                                "effective_fps": effective_fps,
                                "learned": {
                                    "profile": profile_key,
                                    "learned_max_fps": learned_max,
                                },
                                "version": settings.version,
                            },
                        }
                    )
                    last_settings_version = settings.version

                # If not running, send status occasionally and sleep a bit.
                if not settings.running:
                    now = time.time()
                    if now - last_status_sent > 1.0:
                        await websocket.send_json(
                            {
                                "type": "status",
                                "running": False,
                                "settings": {
                                    "running": False,
                                    "version": settings.version,
                                },
                                "ts": now,
                            }
                        )
                        last_status_sent = now
                    await asyncio.sleep(0.05)
                    continue

                start = time.perf_counter()
                # Render to packed RGB bytes off the event loop (fast to stream).
                w, h, rgb = await asyncio.to_thread(lambda: renderer.render_rgb_bytes(settings))
                now = time.time()

                # Binary frame format:
                # - 8-byte header: uint16 width, uint16 height, uint32 seq (little-endian)
                # - followed by width*height*3 bytes RGB
                header = (
                    int(w).to_bytes(2, "little")
                    + int(h).to_bytes(2, "little")
                    + int(seq).to_bytes(4, "little")
                )
                await websocket.send_bytes(header + rgb)
                seq += 1

                # Measure + auto-tune pacing
                elapsed = time.perf_counter() - start
                if ema_frame_s is None:
                    ema_frame_s = elapsed
                else:
                    # EMA with moderate smoothing; favors recent performance.
                    ema_frame_s = 0.90 * ema_frame_s + 0.10 * elapsed

                # Requested fps acts as minimum "feel" knob; max_fps caps upward.
                requested_fps = float(max(1, settings.stream.fps))
                max_fps = float(max(1, settings.stream.max_fps))

                if settings.stream.auto_fps:
                    # Keep headroom so we don't jitter: target dt ~ 15% above EMA.
                    headroom_dt = (ema_frame_s or 0.0) * 1.15
                    target_dt = max(1.0 / max_fps, headroom_dt, 1.0 / requested_fps)
                else:
                    target_dt = 1.0 / requested_fps

                effective_fps = 1.0 / target_dt if target_dt > 0 else 0.0

                # Auto-learn: persist a sustainable max_fps per matrix size.
                if settings.stream.auto_learn and ema_frame_s:
                    now_s = time.time()
                    # update at most every ~5s
                    if now_s - learn_last_update_at > 5.0:
                        sustainable = int(max(1, min(120, (1.0 / (ema_frame_s * 1.15)) if ema_frame_s > 0 else 1)))
                        # Hysteresis: only write meaningful changes
                        if learned_max is None or abs(sustainable - learned_max) >= 2:
                            perf_service.set_learned_max_fps(
                                settings.matrix.width,
                                settings.matrix.height,
                                settings.art.pattern,
                                sustainable,
                            )
                            learned_max = sustainable
                        learn_last_update_at = now_s

                sleep_for = target_dt - elapsed
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)
                else:
                    # If we're behind, yield control briefly.
                    await asyncio.sleep(0)
        except WebSocketDisconnect:
            return

    return router

