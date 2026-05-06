from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.frame import Frame


class DisplayAdapter(ABC):
    """
    Abstract display adapter.

    For the MVP we only implement the simulator path (WebSocket->browser),
    but this interface is meant to be implemented later by hardware outputs.
    """

    @abstractmethod
    async def send_frame(self, frame: Frame) -> None:
        raise NotImplementedError

