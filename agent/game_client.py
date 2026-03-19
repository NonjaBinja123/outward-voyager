"""
WebSocket client that connects to the in-game mod (WebSocketServer.cs).
Sends commands and receives game state / events.
"""
import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)


class GameClient:
    def __init__(self, host: str, port: int) -> None:
        self._uri = f"ws://{host}:{port}/"
        self._ws: ClientConnection | None = None
        self._handlers: dict[str, list[Callable]] = {}
        self._connected = asyncio.Event()

    def on(self, msg_type: str, handler: Callable[[dict], Coroutine]) -> None:
        self._handlers.setdefault(msg_type, []).append(handler)

    async def connect(self) -> None:
        while True:
            try:
                self._ws = await websockets.connect(
                    self._uri,
                    ping_interval=None,
                    ping_timeout=None,
                    max_size=10 * 1024 * 1024,  # 10 MB — scan payloads can be large
                )
                self._connected.set()
                logger.info(f"Connected to game mod at {self._uri}")
                for handler in self._handlers.get("connected", []):
                    await handler({})
                await self._receive_loop()
            except Exception as e:
                self._connected.clear()
                self._ws = None  # prevent stale sends while reconnecting
                logger.warning(f"Game connection lost: {e}. Retrying in 5s...")
                await asyncio.sleep(5)

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            try:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")
                for handler in self._handlers.get(msg_type, []):
                    await handler(msg)
                for handler in self._handlers.get("*", []):
                    await handler(msg)
            except Exception as e:
                logger.warning(f"Message handling error: {e}")

    async def send(self, action: str, params: dict[str, Any] | None = None) -> None:
        await self._connected.wait()
        if self._ws is None:
            logger.warning(f"send({action}): no active connection, dropping")
            return
        try:
            payload = json.dumps({"action": action, "params": params or {}})
            await self._ws.send(payload)
        except Exception as e:
            logger.warning(f"send({action}) failed: {e}")

    async def request_state(self) -> None:
        await self.send("get_state")

    async def say(self, message: str) -> None:
        await self.send("say", {"message": message})

    async def move(self, direction: str, distance: float = 5.0) -> None:
        await self.send("move", {"direction": direction, "distance": distance})

    async def navigate_to(self, x: float, y: float, z: float, run: bool = False) -> None:
        await self.send("navigate_to", {"x": x, "y": y, "z": z, "run": run})

    async def navigate_cancel(self) -> None:
        await self.send("navigate_cancel")

    async def scan_nearby(self, radius: float = 30.0) -> None:
        await self.send("scan_nearby", {"radius": radius})

    async def interact(self, radius: float = 3.0) -> None:
        await self.send("interact", {"radius": radius})

    async def take_item(self, name: str = "", item_id: str = "") -> None:
        await self.send("take_item", {"name": name, "id": item_id})

    async def set_autonomous(self, enabled: bool) -> None:
        await self.send("set_autonomous", {"enabled": enabled})

    async def use_item(self, name: str) -> None:
        await self.send("use_item", {"name": name})

    async def trigger_interaction(self, uid: str = "") -> None:
        await self.send("trigger_interaction", {"uid": uid})

    async def equip_item(self, name: str) -> None:
        await self.send("equip_item", {"name": name})

    async def read_skills(self) -> None:
        await self.send("read_skills")

    async def face_point(self, x: float, y: float, z: float) -> None:
        await self.send("face_point", {"x": x, "y": y, "z": z})
