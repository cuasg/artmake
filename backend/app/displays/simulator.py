from __future__ import annotations

from app.displays.base import DisplayAdapter
from app.models.frame import Frame


class SimulatorDisplay(DisplayAdapter):
    """
    Placeholder adapter for the simulator.

    The actual transport to the browser is handled by the FastAPI WebSocket
    endpoint; this adapter exists so the rest of the system doesn't assume
    a particular output type.
    """

    async def send_frame(self, frame: Frame) -> None:
        return None

