from __future__ import annotations

"""
Route incoming universal protocol messages to the appropriate orchestrator handler.

The dispatcher receives parsed MessageEnvelope objects and calls the right
method on whatever handler object is registered. This decouples protocol
parsing from orchestrator logic.
"""

import logging
from typing import Protocol, runtime_checkable

from protocol.adapter import (
    ActionResult,
    AdapterInfo,
    ChatEvent,
    MessageEnvelope,
    MessageType,
    ParseError,
    WorldEvent,
    parse_message,
)
from protocol.registry import AdapterRegistry, ConnectedGame

logger = logging.getLogger(__name__)


@runtime_checkable
class OrchestratorHandler(Protocol):
    """Interface that the orchestrator must implement to receive dispatched messages."""

    def on_game_state(self, state: dict, game_id: str) -> None: ...

    def on_action_result(self, result: ActionResult, game_id: str) -> None: ...

    def on_chat_event(self, event: ChatEvent, game_id: str) -> None: ...

    def on_world_event(self, event: WorldEvent, game_id: str) -> None: ...

    def on_adapter_connected(self, game: ConnectedGame) -> None: ...

    def on_adapter_disconnected(self, game_id: str) -> None: ...


class MessageDispatcher:
    """Routes parsed MessageEnvelope objects to the registered OrchestratorHandler."""

    def __init__(self, registry: AdapterRegistry, handler: OrchestratorHandler) -> None:
        self._registry = registry
        self._handler = handler

    def dispatch(self, envelope: MessageEnvelope) -> None:
        """Route a validated envelope to the appropriate handler method."""
        msg_type = envelope.message_type
        game_id = envelope.game_id
        payload = envelope.payload

        if msg_type == MessageType.ADAPTER_INFO:
            try:
                info = AdapterInfo.model_validate(payload)
            except Exception as exc:
                logger.warning("Failed to parse AdapterInfo payload: %s", exc)
                return
            game = self._registry.register(info)
            self._handler.on_adapter_connected(game)

        elif msg_type == MessageType.GAME_STATE:
            self._handler.on_game_state(payload, game_id)

        elif msg_type == MessageType.ACTION_RESULT:
            try:
                result = ActionResult.model_validate(payload)
            except Exception as exc:
                logger.warning("Failed to parse ActionResult payload: %s", exc)
                return
            self._handler.on_action_result(result, game_id)

        elif msg_type == MessageType.CHAT_EVENT:
            try:
                event = ChatEvent.model_validate(payload)
            except Exception as exc:
                logger.warning("Failed to parse ChatEvent payload: %s", exc)
                return
            self._handler.on_chat_event(event, game_id)

        elif msg_type == MessageType.WORLD_EVENT:
            try:
                event = WorldEvent.model_validate(payload)
            except Exception as exc:
                logger.warning("Failed to parse WorldEvent payload: %s", exc)
                return
            self._handler.on_world_event(event, game_id)

        elif msg_type == MessageType.HEARTBEAT:
            # Heartbeats are silently acknowledged — no handler call needed.
            logger.debug("Heartbeat received from %s", game_id)

        else:
            logger.warning(
                "MessageDispatcher: unhandled message_type '%s' from game '%s'",
                msg_type,
                game_id,
            )

    def on_raw_message(self, raw: str | bytes) -> None:
        """Parse a raw WebSocket message, then dispatch it. Logs on ParseError."""
        try:
            envelope = parse_message(raw)
        except ParseError as exc:
            logger.warning("Failed to parse incoming message: %s", exc)
            return
        self.dispatch(envelope)
