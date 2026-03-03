"""Manages active WebSocket connections for online agents."""

import logging
import uuid

from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections for online agents."""

    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, WebSocket] = {}

    async def connect(self, agent_id: uuid.UUID, ws: WebSocket) -> None:
        self._connections[agent_id] = ws

    async def disconnect(self, agent_id: uuid.UUID) -> None:
        self._connections.pop(agent_id, None)

    async def send_event(self, agent_id: uuid.UUID, event_type: str, payload: dict) -> bool:
        """Send an event to a connected agent. Returns True if delivered."""
        ws = self._connections.get(agent_id)
        if ws is None:
            return False
        try:
            await ws.send_json({
                "type": "event",
                "event_type": event_type,
                "payload": payload,
            })
            return True
        except Exception:
            logger.warning("Failed to send event to agent %s, removing connection", agent_id)
            await self.disconnect(agent_id)
            return False

    def is_connected(self, agent_id: uuid.UUID) -> bool:
        return agent_id in self._connections

    def online_count(self) -> int:
        return len(self._connections)


# Global singleton
manager = ConnectionManager()
