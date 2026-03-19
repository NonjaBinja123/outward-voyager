from __future__ import annotations

"""
Track game adapters connected to the agent.

On connect, each adapter sends adapter_info. The registry stores the current
connected adapter and provides queries like "is move_to supported?" that let
the orchestrator adapt its strategy to whatever game is connected.
"""

import logging
import time
from dataclasses import dataclass, field

from protocol.adapter import ActionType, AdapterInfo, WorldEventType

logger = logging.getLogger(__name__)


@dataclass
class ConnectedGame:
    game_id: str
    game_display_name: str
    adapter_name: str
    adapter_version: str
    supported_actions: set[str]
    supported_world_events: set[str]
    capabilities: dict[str, bool]
    connected_at: float = field(default_factory=time.time)


class AdapterRegistry:
    """Registry of the currently connected game adapter.

    At most one adapter is connected at a time. When a new adapter_info
    message arrives, the previous entry is replaced.
    """

    def __init__(self) -> None:
        self._current: ConnectedGame | None = None

    def register(self, info: AdapterInfo) -> ConnectedGame:
        """Register a new adapter from an AdapterInfo payload. Returns ConnectedGame."""
        game = ConnectedGame(
            game_id=info.game_id,
            game_display_name=info.game_display_name,
            adapter_name=info.adapter_name,
            adapter_version=info.adapter_version,
            supported_actions={a.value for a in info.supported_actions},
            supported_world_events={e.value for e in info.supported_world_events},
            capabilities=dict(info.capabilities),
        )
        self._current = game
        logger.info(
            "Adapter registered: %s (%s v%s) — %d actions, %d events",
            game.game_display_name,
            game.adapter_name,
            game.adapter_version,
            len(game.supported_actions),
            len(game.supported_world_events),
        )
        return game

    def disconnect(self) -> None:
        """Clear the current adapter (called when the WebSocket closes)."""
        if self._current is not None:
            logger.info("Adapter disconnected: %s", self._current.game_id)
        self._current = None

    def current(self) -> ConnectedGame | None:
        return self._current

    def game_id(self) -> str:
        """Return the current game_id, or 'unknown' if nothing is connected."""
        return self._current.game_id if self._current is not None else "unknown"

    def supports_action(self, action_type: str | ActionType) -> bool:
        """Return True if the connected adapter supports the given action."""
        if self._current is None:
            return False
        key = action_type.value if isinstance(action_type, ActionType) else action_type
        return key in self._current.supported_actions

    def supports_event(self, event_type: str | WorldEventType) -> bool:
        """Return True if the connected adapter supports the given world event."""
        if self._current is None:
            return False
        key = event_type.value if isinstance(event_type, WorldEventType) else event_type
        return key in self._current.supported_world_events

    def has_capability(self, cap: str) -> bool:
        """Return True if the connected adapter reports the given capability as True."""
        if self._current is None:
            return False
        return bool(self._current.capabilities.get(cap, False))

    def is_connected(self) -> bool:
        return self._current is not None

    def context_string(self) -> str:
        """Return a human-readable summary of the current connection for LLM prompts."""
        if self._current is None:
            return "No game adapter connected."
        actions = ", ".join(sorted(self._current.supported_actions))
        return (
            f"Connected: {self._current.game_display_name} "
            f"({self._current.adapter_name} v{self._current.adapter_version}). "
            f"Supports: {actions}"
        )
