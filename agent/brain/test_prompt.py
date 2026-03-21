"""
Brain test tool — iterate on prompts and parser without running the full agent.

Usage (run from agent/ directory):
  python -m brain.test_prompt                    # show system + user prompt for idle scenario
  python -m brain.test_prompt --scenario combat  # combat event
  python -m brain.test_prompt --scenario chat    # player chat event
  python -m brain.test_prompt --scenario death   # strategy tier (death)
  python -m brain.test_prompt --live             # actually call the LLM and show the response
  python -m brain.test_prompt --parse-only       # test parser with a hardcoded sample response
  python -m brain.test_prompt --state path.json  # load real game state from a JSON file

Output sections:
  [SYSTEM PROMPT]  — what rules/personality the LLM gets (from prompts.py)
  [USER PROMPT]    — what game state + event the LLM sees (from observation.py + core.py)
  [LLM RESPONSE]   — raw output (only with --live)
  [PARSED PLAN]    — structured action plan after parser.py
  [TOKEN ESTIMATE] — rough token count for both prompts
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

# Allow running from agent/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Fake game states for each scenario ───────────────────────────────────────

def _fake_state(scenario: str) -> dict[str, Any]:
    base = {
        "scene": "ChersoneseNewTerrain",
        "player": {
            "pos_x": 100.0, "pos_y": 0.5, "pos_z": 200.0, "rotation_y": 45.0,
            "health": 85, "max_health": 100,
            "stamina": 60, "max_stamina": 80,
            "food": 70, "max_food": 100,
            "drink": 50, "max_drink": 100,
            "sleep": 90, "max_sleep": 100,
            "in_combat": False, "is_dead": False,
        },
        "nearby_interactions": [
            {"uid": "Chest_001", "label": "Old Chest", "distance": 4.5, "x": 104.0, "z": 202.0},
            {"uid": "NPC_Merchant", "label": "Traveling Merchant", "distance": 7.2, "x": 107.0, "z": 195.0},
        ],
        "inventory": {
            "pouch": [
                {"name": "Waterskin", "quantity": 1},
                {"name": "Travel Ration", "quantity": 3},
            ],
            "equipped": {"MainHand": "Jade-Ite Sword", "Chest": "Mage Armor"},
        },
        "screen_message": "",
    }

    if scenario == "combat":
        base["player"]["in_combat"] = True
        base["player"]["health"] = 35
        base["nearby_interactions"] = [
            {"uid": "Bandit_42", "label": "Bandit", "distance": 3.0, "x": 103.0, "z": 201.0},
        ]

    elif scenario == "death":
        base["player"]["is_dead"] = True
        base["player"]["health"] = 0
        base["nearby_interactions"] = []

    elif scenario == "chat":
        pass  # base state is fine

    return base


def _fake_scene_objects(scenario: str) -> list[dict]:
    if scenario == "death":
        return []
    return [
        {"name": "Berg Gate", "distance": 45.0, "x": 145.0, "y": 0.5, "z": 230.0,
         "has_character": False, "tag": "Structure"},
        {"name": "Cierzo Merchant", "distance": 22.0, "x": 122.0, "y": 0.5, "z": 215.0,
         "has_character": True, "is_dead": False},
        {"name": "Iron Sword", "distance": 12.0, "x": 112.0, "y": 0.0, "z": 208.0,
         "has_character": False, "tag": "Item"},
    ]


# ── Fake registry ─────────────────────────────────────────────────────────────

class _FakeGame:
    game_display_name = "Outward Definitive Edition"
    supported_actions = {
        "navigate_to", "wait_for_arrival", "stop_navigation",
        "trigger_interaction", "take_item", "open_menu", "close_menu",
        "press_key", "use_item", "equip_item", "say", "wait",
    }
    capabilities = {"can_navigate": True, "has_combat": True, "has_inventory": True}


class _FakeRegistry:
    def current(self):
        return _FakeGame()


# ── Sample parser test responses ──────────────────────────────────────────────

_PARSER_SAMPLES = [
    # Good response
    ('good', '{"thinking": "I see a chest nearby, I should open it.", "actions": [{"action": "trigger_interaction", "params": {"uid": "Chest_001"}}], "expect": "Chest opens", "journal": "Found a chest", "request_strategy": false}'),
    # With thinking block (qwen3)
    ('qwen3_thinking', '<think>Let me think about what to do...</think>\n{"thinking": "Navigate to the merchant", "actions": [{"action": "navigate_to", "params": {"x": 122.0, "y": 0.5, "z": 215.0}}], "expect": "Move toward merchant", "request_strategy": false}'),
    # With markdown fence
    ('markdown_fence', '```json\n{"thinking": "Wait for now", "actions": [{"action": "wait", "params": {"seconds": 3.0}}], "expect": "Brief pause", "request_strategy": false}\n```'),
    # JSON embedded in prose
    ('json_in_prose', 'Sure, here is my plan:\n{"thinking": "I should explore", "actions": [{"action": "navigate_to", "params": {"x": 145.0, "y": 0.5, "z": 230.0}}], "expect": "Head toward gate", "request_strategy": false}\nLet me know if you need anything else.'),
    # Missing actions (bad)
    ('bad_no_actions', '{"thinking": "Something went wrong", "expect": "Nothing"}'),
    # Empty string (bad)
    ('bad_empty', ''),
]


# ── Main ──────────────────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Rough estimate: ~4 chars per token."""
    return len(text) // 4


def run_parser_tests():
    from brain.parser import parse

    print("=" * 70)
    print("PARSER TESTS")
    print("=" * 70)
    for label, sample in _PARSER_SAMPLES:
        result = parse(sample)
        status = "PASS" if (result is not None) == (label.startswith("good") or label not in ("bad_no_actions", "bad_empty")) else "FAIL"
        print(f"\n[{label}] -> {status}")
        if result:
            actions = result.get("actions", [])
            print(f"  actions: {[a['action'] for a in actions]}")
            print(f"  thinking: {result.get('thinking', '')[:60]}")
        else:
            print("  result: None")


async def run_live(system: str, user: str):
    """Call the real LLM with the built prompts."""
    # Add agent/ to path so we can import llm_router + config
    import yaml
    from llm_router import LLMRouter

    cfg_path = Path(__file__).parent.parent / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    router = LLMRouter(cfg.get("llm", {}))

    print("\n" + "=" * 70)
    print("CALLING LLM (reactive tier)...")
    print("=" * 70)

    try:
        raw = await router.complete(system, user, task="reactive", max_tokens=2048)
        print("\n[LLM RESPONSE]")
        print(raw)

        from brain.parser import parse
        result = parse(raw)
        print("\n[PARSED PLAN]")
        if result:
            print(json.dumps(result, indent=2))
        else:
            print("  (parse failed)")
    except Exception as e:
        print(f"LLM call failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Brain prompt test tool")
    parser.add_argument("--scenario", choices=["idle", "combat", "chat", "death"],
                        default="idle", help="Which fake scenario to use")
    parser.add_argument("--live", action="store_true", help="Actually call the LLM")
    parser.add_argument("--parse-only", action="store_true", help="Just run parser tests")
    parser.add_argument("--show-system", action="store_true", help="Only show the system prompt")
    parser.add_argument("--state", type=str, help="Path to a real game state JSON file")
    args = parser.parse_args()

    if args.parse_only:
        run_parser_tests()
        return

    # Build prompts
    from brain.prompts import build_system
    from brain.core import Brain
    from brain.observation import Observation
    from event_bus import GameEvent

    use_strategy = args.scenario == "death"
    registry = _FakeRegistry()
    system = build_system(registry, strategy=use_strategy)

    if args.show_system:
        print("=" * 70)
        print(f"SYSTEM PROMPT (strategy={use_strategy})")
        print("=" * 70)
        print(system)
        print(f"\n[~{_estimate_tokens(system)} tokens]")
        return

    # Load state
    if args.state:
        state = json.loads(Path(args.state).read_text())
    else:
        state = _fake_state(args.scenario)

    # Build event
    event_map = {
        "idle": GameEvent("idle_timeout"),
        "combat": GameEvent("combat_entered"),
        "chat": GameEvent("player_chat", {"text": "Hey Voyager, how are you?", "speaker": "Josh"}),
        "death": GameEvent("death"),
    }
    event = event_map[args.scenario]

    # Build observation
    obs = Observation(
        state=state,
        recent_journal=["Woke up in Cierzo, explored the beach.", "Found a chest but it was empty."],
        active_goals=["Explore the surrounding area", "Find food supplies"],
        pending_chat=["Josh said: Hey Voyager, how are you?"] if args.scenario == "chat" else [],
        scene_objects=_fake_scene_objects(args.scenario),
    )

    # Build user prompt using Brain's _build_user directly
    brain = Brain(llm=None, registry=registry)
    user = brain._build_user(event, obs)

    print("=" * 70)
    print(f"SYSTEM PROMPT (scenario={args.scenario}, strategy={use_strategy})")
    print("=" * 70)
    print(system)

    print("\n" + "=" * 70)
    print("USER PROMPT")
    print("=" * 70)
    print(user)

    sys_tokens = _estimate_tokens(system)
    usr_tokens = _estimate_tokens(user)
    print(f"\n[TOKEN ESTIMATE: system={sys_tokens}, user={usr_tokens}, total={sys_tokens+usr_tokens}]")

    if args.live:
        asyncio.run(run_live(system, user))


if __name__ == "__main__":
    main()
