# Voyager Universal WebSocket Protocol — v1.0

This document is the authoritative specification for the WebSocket protocol
between the Voyager agent and any game adapter. Adapters must implement this
spec to be Voyager-compatible.

---

## Overview

```
Agent (Python) ←── Universal WebSocket ──→ VoyagerBridge.dll
                                                    ↕
                                         [Game]Adapter.dll
                                                    ↕
                                            Game internals
```

- The agent always speaks this protocol; it never knows which game it's talking to.
- Every adapter sends `adapter_info` on connect, declaring what the game supports.
- The agent adapts its strategy to the declared capabilities.
- For non-Unity games: any binary implementing this protocol spec is compatible.

---

## Message Envelope

Every message — in both directions — is a JSON object with this structure:

```json
{
  "voyager_protocol": "1.0",
  "message_id": "<uuid-v4>",
  "timestamp_utc": "<ISO 8601, e.g. 2026-03-19T12:00:00Z>",
  "message_type": "<type>",
  "game_id": "<game-id>",
  "payload": { ... }
}
```

| Field | Type | Notes |
|---|---|---|
| `voyager_protocol` | string | Always `"1.0"` — bump minor for backwards-compatible additions |
| `message_id` | string | UUID v4, unique per message |
| `timestamp_utc` | string | ISO 8601 UTC timestamp of when the message was created |
| `message_type` | string | See message type table below |
| `game_id` | string | Stable identifier for the connected game, e.g. `"outward_definitive"` |
| `payload` | object | Type-specific content |

---

## Message Types

| Type | Direction | Description |
|---|---|---|
| `adapter_info` | adapter → agent | Sent immediately on WebSocket connect |
| `game_state` | adapter → agent | Periodic push of full game state (every ~2s) |
| `action_command` | agent → adapter | Agent requests a game action |
| `action_result` | adapter → agent | Outcome of the most recent action command |
| `chat_event` | adapter → agent | Player sent a chat message in-game |
| `agent_chat_send` | agent → adapter | Agent sends a chat message to in-game chat |
| `world_event` | adapter → agent | Significant game event occurred |
| `heartbeat` | bidirectional | Keep-alive ping; payload is `{}` |

---

## Payload Schemas

### `adapter_info`

Sent by the adapter on WebSocket connect. Declares what the game supports.

```json
{
  "adapter_name": "OutwardAdapter",
  "adapter_version": "1.0.0",
  "game_id": "outward_definitive",
  "game_display_name": "Outward Definitive Edition",
  "supported_actions": ["move_to", "attack_target", "use_item", "interact_entity", "dodge", "rest"],
  "supported_world_events": ["entity_died", "location_entered", "death", "item_spawned"],
  "capabilities": {
    "has_mana": true,
    "has_stamina": true,
    "has_crafting": true,
    "has_quests": true,
    "has_in_game_chat": true
  }
}
```

### `game_state`

Full snapshot of the world state. All sub-objects are optional; adapters emit
what they have. The agent handles missing fields gracefully.

```json
{
  "agent": {
    "health": 85.0,
    "max_health": 100.0,
    "stamina": 60.0,
    "max_stamina": 100.0,
    "mana": 40.0,
    "max_mana": 80.0,
    "is_in_combat": false,
    "is_dead": false,
    "status_effects": ["Hungry", "Tired"],
    "position": { "x": 123.4, "y": 0.0, "z": -55.2 },
    "facing": "NE"
  },
  "environment": {
    "location_id": "ChersoneseRegion",
    "location_display_name": "Chersonese",
    "weather": "Sunny",
    "time_of_day": "Morning",
    "is_indoors": false
  },
  "inventory": {
    "currency": 150,
    "carry_weight": 12.5,
    "max_carry_weight": 30.0,
    "equipped": [
      { "slot": "RightHand", "item_id": "IronSword", "display_name": "Iron Sword" }
    ],
    "backpack": [
      { "item_id": "HealingPotion", "display_name": "Healing Potion", "count": 3 }
    ]
  },
  "nearby_entities": [
    {
      "entity_id": "Bandit_01",
      "display_name": "Bandit",
      "entity_type": "enemy",
      "health_fraction": 1.0,
      "distance": 8.5,
      "is_hostile": true,
      "position": { "x": 131.0, "y": 0.0, "z": -55.2 }
    }
  ],
  "quest_state": [
    { "quest_id": "Tutorial01", "display_name": "Survival Basics", "status": "active" }
  ]
}
```

### `action_command`

Agent requests an action. The adapter executes it and returns `action_result`.

```json
{
  "action_type": "move_to",
  "params": {
    "x": 150.0,
    "y": 0.0,
    "z": -60.0
  }
}
```

```json
{
  "action_type": "use_item",
  "params": { "item_id": "HealingPotion" }
}
```

```json
{
  "action_type": "attack_target",
  "params": { "entity_id": "Bandit_01" }
}
```

```json
{
  "action_type": "send_chat",
  "params": { "text": "Hello!" }
}
```

### `action_result`

```json
{
  "action_type": "move_to",
  "status": "ok",
  "message": "Arrived at destination",
  "data": {}
}
```

| `status` | Meaning |
|---|---|
| `ok` | Action succeeded |
| `failed` | Action was attempted but failed |
| `unsupported` | This adapter does not support this action type |
| `partial` | Action partially succeeded (e.g. moved toward but didn't reach target) |

### `chat_event`

```json
{
  "sender": "Josh",
  "text": "What are you doing?",
  "channel": "local",
  "timestamp_utc": "2026-03-19T12:34:56Z"
}
```

### `agent_chat_send`

```json
{
  "text": "Exploring the ruins near the river."
}
```

### `world_event`

```json
{
  "event_type": "location_entered",
  "data": {
    "location_id": "ChersoneseRegion",
    "location_display_name": "Chersonese"
  }
}
```

```json
{
  "event_type": "entity_died",
  "data": {
    "entity_id": "Bandit_01",
    "display_name": "Bandit",
    "killed_by_agent": true
  }
}
```

---

## Universal Action Types

Adapters report which of these they support in `adapter_info.supported_actions`.
An adapter receiving an unsupported action returns `action_result.status: "unsupported"`.

| Action | Required params | Description |
|---|---|---|
| `move_to` | `x, y, z` | Navigate to world coordinates |
| `attack_target` | `entity_id` | Attack a specific entity |
| `use_item` | `item_id` | Use/consume an item from inventory |
| `equip_item` | `item_id` | Equip item from inventory |
| `unequip_item` | `slot` | Unequip item in given slot |
| `interact_entity` | `entity_id` | Interact with NPC or object |
| `dodge` | _(none)_ | Execute dodge/roll |
| `block` | _(none)_ | Enter/exit blocking stance |
| `jump` | _(none)_ | Jump |
| `rest` | `hours` (optional) | Rest to recover (sleep/camp) |
| `craft_item` | `recipe_id` | Craft an item from a recipe |
| `send_chat` | `text` | Send text to in-game chat |
| `loot_target` | `entity_id` | Loot a dead entity or container |
| `drop_item` | `item_id, count` | Drop item from inventory |

---

## Universal World Event Subtypes

| Event | Key data fields | Description |
|---|---|---|
| `entity_died` | `entity_id, display_name, killed_by_agent` | An entity was killed |
| `item_spawned` | `item_id, display_name, position` | An item appeared in the world |
| `item_despawned` | `item_id` | An item was removed from the world |
| `location_entered` | `location_id, location_display_name` | Agent entered a new area |
| `location_exited` | `location_id` | Agent left an area |
| `weather_changed` | `weather` | Weather changed |
| `time_changed` | `time_of_day` | Time of day changed (Dawn, Morning, …) |
| `quest_updated` | `quest_id, status` | Quest status changed |
| `level_up` | `new_level` | Character gained a level |
| `death` | `location_id` | Agent character died |
| `game_saved` | _(none)_ | Game saved |
| `game_loaded` | _(none)_ | Game loaded / session started |

---

## Cross-Game Skill Tagging

Skills in the SQLite `skills` table carry a `game_scope` column:

| `game_scope` | Meaning |
|---|---|
| `game_specific` | Only works in `source_game_id`; never transferred to a different game |
| `cross_game` | Universal skill (navigation, social, resting, etc.) — migrates to any game |
| `archived` | Was game_specific; source game is no longer connected — kept for research |

Initial cross-game skill categories: `navigation`, `social_interaction`,
`resource_gathering`, `threat_assessment`, `resting`, `inventory_management`.

---

## Versioning Rules

- **Minor bumps** (`1.0` → `1.1`): backwards-compatible additions (new optional payload fields, new message types). Adapters ignore unknown fields.
- **Major bumps** (`1.x` → `2.0`): breaking changes. Adapter and agent must both be updated.
- An adapter receiving a `voyager_protocol` version it doesn't support SHOULD send one `heartbeat` with payload `{"error": "unsupported_protocol_version", "supported": "1.0"}` then close the connection.

---

## Adapter Compliance Checklist

- [ ] Sends `adapter_info` within 2 seconds of WebSocket connect
- [ ] Sends `game_state` at least every 5 seconds while game is running
- [ ] Responds to every `action_command` with an `action_result`
- [ ] Returns `status: "unsupported"` for unimplemented actions (never silently ignores)
- [ ] Responds to `heartbeat` with `heartbeat`
- [ ] Includes correct `game_id` in every envelope
- [ ] Reconnects gracefully if WebSocket is dropped (no action required from agent)
- [ ] `message_id` is unique per message (UUID v4)
- [ ] `timestamp_utc` is valid ISO 8601 UTC

---

*Spec version: 1.0 — created 2026-03-19*
