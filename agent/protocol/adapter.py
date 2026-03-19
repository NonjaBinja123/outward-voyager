from __future__ import annotations

"""
Parse and validate universal Voyager WebSocket protocol messages.

Every message envelope:
{
  "voyager_protocol": "1.0",
  "message_id": "<uuid>",
  "timestamp_utc": "<ISO8601>",
  "message_type": "<type>",
  "game_id": "<game_id>",
  "payload": { ... }
}

Message types (adapter -> agent): adapter_info, game_state, action_result, chat_event, world_event, heartbeat
Message types (agent -> adapter): action_command, agent_chat_send, heartbeat
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "1.0"


class MessageType(str, Enum):
    ADAPTER_INFO = "adapter_info"
    GAME_STATE = "game_state"
    ACTION_COMMAND = "action_command"
    ACTION_RESULT = "action_result"
    CHAT_EVENT = "chat_event"
    AGENT_CHAT_SEND = "agent_chat_send"
    WORLD_EVENT = "world_event"
    HEARTBEAT = "heartbeat"


class ActionType(str, Enum):
    MOVE_TO = "move_to"
    ATTACK_TARGET = "attack_target"
    USE_ITEM = "use_item"
    EQUIP_ITEM = "equip_item"
    UNEQUIP_ITEM = "unequip_item"
    INTERACT_ENTITY = "interact_entity"
    DODGE = "dodge"
    BLOCK = "block"
    JUMP = "jump"
    REST = "rest"
    CRAFT_ITEM = "craft_item"
    SEND_CHAT = "send_chat"
    LOOT_TARGET = "loot_target"
    DROP_ITEM = "drop_item"


class WorldEventType(str, Enum):
    ENTITY_DIED = "entity_died"
    ITEM_SPAWNED = "item_spawned"
    ITEM_DESPAWNED = "item_despawned"
    LOCATION_ENTERED = "location_entered"
    LOCATION_EXITED = "location_exited"
    WEATHER_CHANGED = "weather_changed"
    TIME_CHANGED = "time_changed"
    QUEST_UPDATED = "quest_updated"
    LEVEL_UP = "level_up"
    DEATH = "death"
    GAME_SAVED = "game_saved"
    GAME_LOADED = "game_loaded"


class ParseError(Exception):
    """Raised when a raw message cannot be parsed or validated."""


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class MessageEnvelope(BaseModel):
    voyager_protocol: str
    message_id: str
    timestamp_utc: str
    message_type: MessageType
    game_id: str
    payload: dict[str, Any]


class AdapterInfo(BaseModel):
    adapter_name: str
    adapter_version: str
    game_id: str
    game_display_name: str
    supported_actions: list[ActionType]
    supported_world_events: list[WorldEventType]
    capabilities: dict[str, bool] = Field(default_factory=dict)


class GameState(BaseModel):
    agent: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)
    inventory: dict[str, Any] = Field(default_factory=dict)
    nearby_entities: list[dict[str, Any]] = Field(default_factory=list)
    quest_state: list[dict[str, Any]] = Field(default_factory=list)


class ActionCommand(BaseModel):
    action_type: ActionType
    params: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    action_type: ActionType
    status: str  # "ok" | "failed" | "unsupported" | "partial"
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class ChatEvent(BaseModel):
    sender: str
    text: str
    channel: str = "local"
    timestamp_utc: str = ""


class WorldEvent(BaseModel):
    event_type: WorldEventType
    data: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def parse_message(raw: str | bytes) -> MessageEnvelope:
    """Parse and validate a raw JSON WebSocket message into a MessageEnvelope.

    Raises ParseError on any failure.
    """
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParseError(f"Failed to decode message: {exc}") from exc

    try:
        envelope = MessageEnvelope.model_validate(data)
    except Exception as exc:
        raise ParseError(f"Message validation failed: {exc}") from exc

    return envelope


def make_envelope(message_type: MessageType, game_id: str, payload: dict[str, Any]) -> str:
    """Create a JSON-serialized MessageEnvelope with a new UUID and current UTC timestamp."""
    envelope = {
        "voyager_protocol": PROTOCOL_VERSION,
        "message_id": str(uuid.uuid4()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "message_type": message_type.value,
        "game_id": game_id,
        "payload": payload,
    }
    return json.dumps(envelope)


def make_action_command(
    action_type: ActionType,
    game_id: str,
    params: dict[str, Any] | None = None,
) -> str:
    """Convenience function: create a serialized action_command envelope."""
    if params is None:
        params = {}
    payload = ActionCommand(action_type=action_type, params=params).model_dump()
    return make_envelope(MessageType.ACTION_COMMAND, game_id, payload)
