"""
Protocol compliance tests for the universal Voyager WebSocket protocol.

Tests parse_message(), make_envelope(), make_action_command(), AdapterRegistry,
and MessageDispatcher using a mock adapter to validate all message type round-trips.
"""
from __future__ import annotations

import json
import time
import unittest
from unittest.mock import MagicMock, patch

# Adjust path so tests can import from agent/
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from protocol.adapter import (
    PROTOCOL_VERSION,
    ActionType,
    AdapterInfo,
    ChatEvent,
    MessageType,
    WorldEventType,
    make_action_command,
    make_envelope,
    parse_message,
    ParseError,
    ActionResult,
    WorldEvent,
)
from protocol.registry import AdapterRegistry, ConnectedGame
from protocol.dispatcher import MessageDispatcher


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_raw_envelope(
    message_type: str,
    payload: dict,
    game_id: str = "test_game",
    protocol: str = "1.0",
) -> str:
    import uuid
    return json.dumps({
        "voyager_protocol": protocol,
        "message_id": str(uuid.uuid4()),
        "timestamp_utc": "2026-03-19T12:00:00Z",
        "message_type": message_type,
        "game_id": game_id,
        "payload": payload,
    })


def _adapter_info_payload() -> dict:
    return {
        "adapter_name": "TestAdapter",
        "adapter_version": "1.0.0",
        "game_id": "test_game",
        "game_display_name": "Test Game",
        "supported_actions": ["move_to", "attack_target", "use_item", "send_chat"],
        "supported_world_events": ["entity_died", "location_entered", "death"],
        "capabilities": {
            "has_mana": False,
            "has_stamina": True,
            "has_crafting": False,
            "has_quests": True,
            "has_in_game_chat": True,
        },
    }


# ── parse_message tests ───────────────────────────────────────────────────────

class TestParseMessage(unittest.TestCase):

    def test_parse_adapter_info(self) -> None:
        raw = _make_raw_envelope("adapter_info", _adapter_info_payload())
        envelope = parse_message(raw)
        self.assertEqual(envelope.message_type, MessageType.ADAPTER_INFO)
        self.assertEqual(envelope.game_id, "test_game")
        self.assertEqual(envelope.voyager_protocol, PROTOCOL_VERSION)

    def test_parse_game_state(self) -> None:
        payload = {
            "agent": {"health": 85.0, "max_health": 100.0, "is_in_combat": False},
            "environment": {"location_id": "Chersonese", "weather": "Sunny"},
        }
        raw = _make_raw_envelope("game_state", payload)
        envelope = parse_message(raw)
        self.assertEqual(envelope.message_type, MessageType.GAME_STATE)
        self.assertIn("agent", envelope.payload)

    def test_parse_action_result_ok(self) -> None:
        payload = {"action_type": "move_to", "status": "ok", "message": "arrived", "data": {}}
        raw = _make_raw_envelope("action_result", payload)
        envelope = parse_message(raw)
        self.assertEqual(envelope.message_type, MessageType.ACTION_RESULT)

    def test_parse_action_result_unsupported(self) -> None:
        payload = {"action_type": "craft_item", "status": "unsupported", "message": "", "data": {}}
        raw = _make_raw_envelope("action_result", payload)
        envelope = parse_message(raw)
        self.assertEqual(envelope.message_type, MessageType.ACTION_RESULT)

    def test_parse_chat_event(self) -> None:
        payload = {"sender": "Josh", "text": "Hello!", "channel": "local", "timestamp_utc": ""}
        raw = _make_raw_envelope("chat_event", payload)
        envelope = parse_message(raw)
        self.assertEqual(envelope.message_type, MessageType.CHAT_EVENT)

    def test_parse_world_event_entity_died(self) -> None:
        payload = {"event_type": "entity_died", "data": {"entity_id": "Bandit_01", "killed_by_agent": True}}
        raw = _make_raw_envelope("world_event", payload)
        envelope = parse_message(raw)
        self.assertEqual(envelope.message_type, MessageType.WORLD_EVENT)

    def test_parse_heartbeat(self) -> None:
        raw = _make_raw_envelope("heartbeat", {})
        envelope = parse_message(raw)
        self.assertEqual(envelope.message_type, MessageType.HEARTBEAT)

    def test_parse_invalid_json(self) -> None:
        with self.assertRaises(ParseError):
            parse_message("not json {{{")

    def test_parse_missing_field(self) -> None:
        bad = json.dumps({"voyager_protocol": "1.0", "payload": {}})
        with self.assertRaises(ParseError):
            parse_message(bad)

    def test_parse_unknown_message_type(self) -> None:
        # Unknown message types should raise ParseError (enum validation fails)
        raw = _make_raw_envelope("totally_unknown_type", {})
        with self.assertRaises(ParseError):
            parse_message(raw)

    def test_parse_bytes(self) -> None:
        raw = _make_raw_envelope("heartbeat", {})
        envelope = parse_message(raw.encode("utf-8"))
        self.assertEqual(envelope.message_type, MessageType.HEARTBEAT)


# ── make_envelope tests ───────────────────────────────────────────────────────

class TestMakeEnvelope(unittest.TestCase):

    def test_make_envelope_round_trip(self) -> None:
        raw = make_envelope(MessageType.HEARTBEAT, "test_game", {})
        data = json.loads(raw)
        self.assertEqual(data["message_type"], "heartbeat")
        self.assertEqual(data["game_id"], "test_game")
        self.assertEqual(data["voyager_protocol"], PROTOCOL_VERSION)
        self.assertIn("message_id", data)
        self.assertIn("timestamp_utc", data)

    def test_make_action_command_move_to(self) -> None:
        raw = make_action_command(ActionType.MOVE_TO, "test_game", {"x": 10.0, "y": 0.0, "z": -5.0})
        envelope = parse_message(raw)
        self.assertEqual(envelope.message_type, MessageType.ACTION_COMMAND)
        self.assertEqual(envelope.payload["action_type"], "move_to")
        self.assertAlmostEqual(envelope.payload["params"]["x"], 10.0)

    def test_make_action_command_unique_ids(self) -> None:
        raw1 = make_action_command(ActionType.DODGE, "test_game")
        raw2 = make_action_command(ActionType.DODGE, "test_game")
        id1 = json.loads(raw1)["message_id"]
        id2 = json.loads(raw2)["message_id"]
        self.assertNotEqual(id1, id2)


# ── AdapterRegistry tests ─────────────────────────────────────────────────────

class TestAdapterRegistry(unittest.TestCase):

    def setUp(self) -> None:
        self.registry = AdapterRegistry()
        info_payload = _adapter_info_payload()
        raw = _make_raw_envelope("adapter_info", info_payload)
        envelope = parse_message(raw)
        info = AdapterInfo(**envelope.payload)
        self.game = self.registry.register(info)

    def test_register_sets_game_id(self) -> None:
        self.assertEqual(self.registry.game_id(), "test_game")

    def test_is_connected(self) -> None:
        self.assertTrue(self.registry.is_connected())

    def test_supports_action_true(self) -> None:
        self.assertTrue(self.registry.supports_action("move_to"))
        self.assertTrue(self.registry.supports_action(ActionType.MOVE_TO))

    def test_supports_action_false(self) -> None:
        self.assertFalse(self.registry.supports_action("craft_item"))
        self.assertFalse(self.registry.supports_action(ActionType.CRAFT_ITEM))

    def test_supports_event_true(self) -> None:
        self.assertTrue(self.registry.supports_event("entity_died"))
        self.assertTrue(self.registry.supports_event(WorldEventType.ENTITY_DIED))

    def test_has_capability(self) -> None:
        self.assertTrue(self.registry.has_capability("has_stamina"))
        self.assertFalse(self.registry.has_capability("has_mana"))
        self.assertFalse(self.registry.has_capability("nonexistent"))

    def test_disconnect_clears_state(self) -> None:
        self.registry.disconnect()
        self.assertFalse(self.registry.is_connected())
        self.assertEqual(self.registry.game_id(), "unknown")

    def test_context_string_contains_game_name(self) -> None:
        ctx = self.registry.context_string()
        self.assertIn("Test Game", ctx)
        self.assertIn("TestAdapter", ctx)

    def test_supports_action_when_disconnected(self) -> None:
        self.registry.disconnect()
        self.assertFalse(self.registry.supports_action("move_to"))


# ── MessageDispatcher tests ───────────────────────────────────────────────────

class TestMessageDispatcher(unittest.TestCase):

    def setUp(self) -> None:
        self.registry = AdapterRegistry()
        self.handler = MagicMock()
        self.dispatcher = MessageDispatcher(self.registry, self.handler)

    def test_dispatch_adapter_info_registers(self) -> None:
        raw = _make_raw_envelope("adapter_info", _adapter_info_payload())
        envelope = parse_message(raw)
        self.dispatcher.dispatch(envelope)
        self.handler.on_adapter_connected.assert_called_once()
        self.assertTrue(self.registry.is_connected())

    def test_dispatch_game_state(self) -> None:
        payload = {"agent": {"health": 80.0}, "environment": {}}
        raw = _make_raw_envelope("game_state", payload)
        envelope = parse_message(raw)
        self.dispatcher.dispatch(envelope)
        self.handler.on_game_state.assert_called_once()
        call_args = self.handler.on_game_state.call_args
        self.assertIn("agent", call_args[0][0])

    def test_dispatch_chat_event(self) -> None:
        payload = {"sender": "Josh", "text": "Hello", "channel": "local", "timestamp_utc": ""}
        raw = _make_raw_envelope("chat_event", payload)
        envelope = parse_message(raw)
        self.dispatcher.dispatch(envelope)
        self.handler.on_chat_event.assert_called_once()

    def test_dispatch_action_result(self) -> None:
        payload = {"action_type": "move_to", "status": "ok", "message": "", "data": {}}
        raw = _make_raw_envelope("action_result", payload)
        envelope = parse_message(raw)
        self.dispatcher.dispatch(envelope)
        self.handler.on_action_result.assert_called_once()

    def test_dispatch_world_event(self) -> None:
        payload = {"event_type": "location_entered", "data": {"location_id": "Chersonese"}}
        raw = _make_raw_envelope("world_event", payload)
        envelope = parse_message(raw)
        self.dispatcher.dispatch(envelope)
        self.handler.on_world_event.assert_called_once()

    def test_dispatch_heartbeat_no_handler(self) -> None:
        raw = _make_raw_envelope("heartbeat", {})
        envelope = parse_message(raw)
        # Should not raise; heartbeat is silently accepted
        self.dispatcher.dispatch(envelope)

    def test_on_raw_message_parse_error_doesnt_crash(self) -> None:
        # Should log error but not raise
        self.dispatcher.on_raw_message("this is not json {{")
        self.handler.on_game_state.assert_not_called()

    def test_full_round_trip_adapter_then_state(self) -> None:
        """Simulate a real connection: adapter_info → game_state → chat → action_result."""
        msgs = [
            _make_raw_envelope("adapter_info", _adapter_info_payload()),
            _make_raw_envelope("game_state", {"agent": {"health": 90.0}, "environment": {}}),
            _make_raw_envelope("chat_event", {"sender": "Josh", "text": "Hi", "channel": "local", "timestamp_utc": ""}),
            _make_raw_envelope("action_result", {"action_type": "move_to", "status": "ok", "message": "", "data": {}}),
            _make_raw_envelope("heartbeat", {}),
        ]
        for raw in msgs:
            self.dispatcher.on_raw_message(raw)

        self.handler.on_adapter_connected.assert_called_once()
        self.handler.on_game_state.assert_called_once()
        self.handler.on_chat_event.assert_called_once()
        self.handler.on_action_result.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
