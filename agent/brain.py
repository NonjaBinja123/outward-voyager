"""
Brain — game-agnostic LLM interaction layer.

Receives a GameEvent + full observation bundle, calls the right LLM tier,
parses the response, and returns a structured action plan.

NO game knowledge. NO Outward-specific logic.
The system prompt is built from adapter_info + dynamic game state only.
"""
import json
import logging
import re
from typing import Any

from event_bus import GameEvent

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────
# The only text in this file that the LLM sees.
# Intentionally game-agnostic — all game knowledge comes from adapter context.

_GROUNDING_RULES = """\
GROUNDING RULES — you must follow these absolutely:
1. NEARBY OBJECTS is the only source of interactable UIDs. Never invent a UID.
   If the list says "(none)", there is nothing to interact with. Do not pretend otherwise.
2. navigate_to requires x/y/z floats from the game state. Never use a name or UID as a target.
3. Do NOT draw on training knowledge about this game (wikis, guides, quest names, map layout).
   You only know what appears in this observation. Treat every session as a fresh start.
4. If you have nothing useful to do, respond with a single wait action. Do not fabricate a plan.
5. If a UID appears in STUCK INTERACTIONS, never attempt trigger_interaction with it again.
   Use close_menu, navigate_to a scene object, or wait instead.
6. Prefer navigate_to (using pos coordinates from SCENE OBJECTS) to move toward interesting things.
   Then trigger_interaction or take_item once you are within range.
7. If PENDING PLAYER MESSAGES is non-empty, consider whether the situation allows a reply.
   If not in combat or immediate danger, responding with say is natural and encouraged.
"""

_REACTIVE_SYSTEM = """\
You are Voyager, an autonomous AI agent inhabiting a character in a video game.
You make your own decisions. You learn from experience. You are curious.

CONNECTED GAME: {game_name}
AVAILABLE ACTIONS: {available_actions}
GAME CAPABILITIES: {capabilities}

{grounding_rules}
You will receive observations about the current game state and a triggering event.
Respond with a JSON action plan using ONLY actions from AVAILABLE ACTIONS.

Action parameter schemas (use EXACTLY these, no other keys):
  navigate_to         : {{"x": <float>, "y": <float>, "z": <float>}}
  wait_for_arrival    : {{}}
  stop_navigation     : {{}}
  trigger_interaction : {{"uid": "<uid from NEARBY OBJECTS only>"}}
  take_item           : {{"item_name": "<item name to pick up from the ground>"}}
  open_menu           : {{"menu": "inventory"|"map"|"character"|"skills"}}
  close_menu          : {{}}
  press_key           : {{"key": "<single key: f, e, space, etc.>"}}
  use_item            : {{"item_name": "<name from INVENTORY>"}}
  equip_item          : {{"item_name": "<name from INVENTORY>"}}
  say                 : {{"text": "<message>"}}
  wait                : {{"seconds": <float>}}

Response format (JSON only, no markdown):
{{
  "thinking": "<one sentence of internal reasoning>",
  "actions": [{{"action": "<name>", "params": {{...}}}}],
  "expect": "<what you expect to happen>",
  "journal": "<optional one-sentence log entry>",
  "request_strategy": false
}}

Set request_strategy true only when genuinely lost or after a major event (death, big discovery).
"""

_STRATEGY_SYSTEM = """\
You are Voyager, an autonomous AI agent inhabiting a character in a video game.
You have a moment to reflect deeply on your situation and set direction.

CONNECTED GAME: {game_name}
AVAILABLE ACTIONS: {available_actions}
GAME CAPABILITIES: {capabilities}

{grounding_rules}
Respond with a JSON strategy plan (JSON only, no markdown):
{{
  "thinking": "<multi-sentence reflection>",
  "actions": [{{"action": "<name>", "params": {{...}}}}],
  "expect": "<what you expect to happen>",
  "journal": "<journal entry summarizing thoughts and direction>",
  "goals": {{
    "set": ["<new goal>"],
    "complete": ["<completed goal>"],
    "drop": ["<goal to abandon>"]
  }},
  "request_strategy": false
}}

Think about:
- What do I actually observe right now? What scene am I in?
- Are there characters (NPCs) or items nearby I should approach?
- What concrete goals should I set? Be specific: "navigate to NPC X at pos (x,z)" or "explore the area around my current position".
- If I have no active goals, my priority is to navigate toward a CHARACTERS NEARBY entry, or if none, a SCENE OBJECTS entry.
"""


class Brain:
    """
    Packages observations into LLM prompts, calls the right tier, returns action plan.

    Tiers:
      - reactive: Gemini Flash — fast, cheap, for moment-to-moment decisions
      - strategy: Sonnet — deep, for reflection/goal-setting (agent-triggered or on death)
    """

    # Events that always escalate to strategy tier
    STRATEGY_EVENTS = {"death", "strategy_request"}

    def __init__(self, llm: Any, registry: Any) -> None:
        """
        llm: LLMRouter instance
        registry: AdapterRegistry instance
        """
        self._llm = llm
        self._registry = registry

    # ── Main entry point ──────────────────────────────────────────────────────

    async def think(
        self,
        event: GameEvent,
        obs: "Observation",
    ) -> dict[str, Any] | None:
        """
        Given a triggering event and observation bundle, call the LLM and return
        a parsed action plan dict, or None on failure.
        """
        use_strategy = event.name in self.STRATEGY_EVENTS
        system = self._build_system(strategy=use_strategy)
        user = self._build_user(event, obs)
        task = "strategy" if use_strategy else "reactive"
        # Ollama is free/local — no reason to cap output tokens tightly.
        # qwen3:14b thinking (strategy) can use 8K+ tokens; give it room.
        max_tokens = 16384 if use_strategy else 8192

        logger.info(f"[Brain] Think — event={event.name!r} tier={task}")
        try:
            raw = await self._llm.complete(system, user, task=task, max_tokens=max_tokens)
        except Exception as e:
            logger.warning(f"[Brain] LLM call failed: {e}")
            return None

        return self._parse(raw)

    # ── Prompt builders ───────────────────────────────────────────────────────

    # Fallback action list when no adapter_info has been received
    _FALLBACK_ACTIONS = (
        "navigate_to, wait_for_arrival, stop_navigation, "
        "trigger_interaction, take_item, open_menu, close_menu, "
        "press_key, use_item, equip_item, say, wait, wait_for_state"
    )

    def _build_system(self, *, strategy: bool) -> str:
        game = self._registry.current()
        game_name = game.game_display_name if game else "Unknown Game"
        actions = ", ".join(sorted(game.supported_actions)) if game else self._FALLBACK_ACTIONS
        caps = str(game.capabilities) if game else "{}"
        template = _STRATEGY_SYSTEM if strategy else _REACTIVE_SYSTEM
        return template.format(
            game_name=game_name,
            available_actions=actions,
            capabilities=caps,
            grounding_rules=_GROUNDING_RULES,
        )

    def _build_user(self, event: GameEvent, obs: "Observation") -> str:
        lines = [
            f"EVENT: {event.name}",
        ]
        if event.data:
            lines.append(f"EVENT DATA: {json.dumps(event.data)}")

        lines.append("")
        lines.append("GAME STATE:")
        lines.append(obs.state_summary())

        if obs.recent_journal:
            lines.append("")
            lines.append("RECENT MEMORY:")
            for entry in obs.recent_journal:
                lines.append(f"  - {entry}")

        if obs.active_goals:
            lines.append("")
            lines.append("ACTIVE GOALS:")
            for g in obs.active_goals:
                lines.append(f"  - {g}")

        if obs.pending_chat:
            lines.append("")
            lines.append("PENDING PLAYER MESSAGES:")
            for msg in obs.pending_chat:
                lines.append(f"  - {msg}")

        if obs.extra_context:
            lines.append("")
            lines.append("ADDITIONAL CONTEXT:")
            lines.append(obs.extra_context)

        return "\n".join(lines)

    # ── Response parsing ──────────────────────────────────────────────────────

    def _parse(self, raw: str) -> dict[str, Any] | None:
        if not raw:
            return None
        # Strip qwen3 / reasoning model thinking blocks before anything else
        text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try each '{' in the text; find the first balanced JSON object that parses.
            # This handles Claude prepending thinking text or prose with stray braces.
            data = None
            for i, ch in enumerate(text):
                if ch != "{":
                    continue
                depth = 0
                for j, c in enumerate(text[i:], i):
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                data = json.loads(text[i:j + 1])
                            except json.JSONDecodeError:
                                pass
                            break
                if data is not None:
                    break
            if data is None:
                logger.warning(f"[Brain] Could not parse LLM response: {text[:200]}")
                return None

        actions = data.get("actions")
        if not isinstance(actions, list):
            logger.warning(f"[Brain] No 'actions' list in response: {data}")
            return None

        logger.debug(f"[Brain] Plan: {data.get('thinking', '')[:80]}")
        return data


# ── Observation bundle ────────────────────────────────────────────────────────

class Observation:
    """
    Packages everything the Brain needs to build an LLM prompt.
    Constructed by the Orchestrator before each think() call.
    """

    def __init__(
        self,
        state: dict[str, Any],
        recent_journal: list[str] | None = None,
        active_goals: list[str] | None = None,
        pending_chat: list[str] | None = None,
        extra_context: str = "",
        scene_objects: list[dict] | None = None,
    ) -> None:
        self._state = state
        self.recent_journal = recent_journal or []
        self.active_goals = active_goals or []
        self.pending_chat = pending_chat or []
        self.extra_context = extra_context
        self.scene_objects = scene_objects or []

    # UIDs that are Unity scene hierarchy containers or placeholder objects.
    # The mod's scan picks these up — filter them out so the LLM never sees them.
    _GARBAGE_UIDS: frozenset[str] = frozenset({
        "Interiors", "Environment", "PlayerHouse", "_SNPC", "Exterior",
        "Dungeon", "Town", "Village", "City", "Interior",
        "Cube", "Sphere", "Cylinder", "Plane", "Quad",  # Unity default primitive names
    })

    @staticmethod
    def _fmt_stat(val: Any, max_val: Any) -> str:
        """Format a stat value, replacing astronomical garbage values with '?'."""
        def _clean(v: Any) -> str:
            try:
                f = float(v)
                if abs(f) > 1e10 or f != f:  # insane value or NaN
                    return "?"
                return f"{f:.0f}"
            except (TypeError, ValueError):
                return "?"
        return f"{_clean(val)}/{_clean(max_val)}"

    def state_summary(self) -> str:
        """Serialize game state to a compact, LLM-readable string."""
        s = self._state
        p = s.get("player", {})
        lines = [
            f"Scene: {s.get('scene', 'unknown')}",
        ]
        if all(k in p for k in ("pos_x", "pos_y", "pos_z")):
            rot = p.get("rotation_y", None)
            rot_str = f"  facing={rot:.0f}°" if rot is not None else ""
            lines.append(
                f"Position: ({p['pos_x']:.1f}, {p['pos_y']:.1f}, {p['pos_z']:.1f}){rot_str}"
            )
        lines += [
            f"Health: {self._fmt_stat(p.get('health'), p.get('max_health'))}",
            f"Stamina: {self._fmt_stat(p.get('stamina'), p.get('max_stamina'))}",
            f"Food: {self._fmt_stat(p.get('food'), p.get('max_food'))}",
            f"Drink: {self._fmt_stat(p.get('drink'), p.get('max_drink'))}",
            f"Sleep: {self._fmt_stat(p.get('sleep'), p.get('max_sleep'))}",
            f"In combat: {p.get('in_combat', False)}",
            f"Dead: {p.get('is_dead', False)}",
        ]
        status = p.get("status_effects", [])
        if status:
            lines.append(f"Status effects: {', '.join(status)}")

        # ── Nearby interactions (can trigger_interaction RIGHT NOW) ──────────
        raw = s.get("nearby_interactions", [])
        player_uid = next(
            (i.get("uid", "") for i in raw if i.get("distance", 999) == 0), ""
        )
        interactable = [
            i for i in raw
            if i.get("uid") != player_uid                # not self
            and i.get("uid") not in self._GARBAGE_UIDS  # not scene containers
        ]
        lines.append("")
        lines.append("INTERACT NOW — use trigger_interaction with these UIDs only:")
        if interactable:
            for obj in interactable:
                uid = obj.get("uid", "?")
                name = obj.get("label") or obj.get("name") or uid
                dist = obj.get("distance", 0)
                x, z = obj.get("x", "?"), obj.get("z", "?")
                lines.append(f"  uid={uid!r}  name={name!r}  dist={dist:.1f}m  pos=({x}, {z})")
        else:
            lines.append("  (none — move closer to something before interacting)")

        # ── Scene objects (visible in area, navigate toward them) ────────────
        if self.scene_objects:
            # Only show objects worth navigating to (more than 8m away — already-nearby objects
            # are already in INTERACT NOW; showing them here causes pointless micro-navigation)
            far_enough = [o for o in self.scene_objects if float(o.get("distance", 999)) > 8]
            characters = [o for o in far_enough if o.get("has_character") and not o.get("is_dead")]
            non_chars = [o for o in far_enough if not o.get("has_character")]

            if characters:
                lines.append("")
                lines.append("CHARACTERS NEARBY — navigate_to their pos then trigger_interaction:")
                for obj in characters[:10]:
                    name = obj.get("name", "?")
                    dist = obj.get("distance", "?")
                    x, y, z = obj.get("x", "?"), obj.get("y", "?"), obj.get("z", "?")
                    lines.append(f"  {name!r}  dist={dist}m  pos=({x}, {y}, {z})")

            if non_chars:
                lines.append("")
                lines.append("SCENE OBJECTS — navigate_to pos to approach:")
                for obj in non_chars[:15]:
                    name = obj.get("name", "?")
                    dist = obj.get("distance", "?")
                    x, y, z = obj.get("x", "?"), obj.get("y", "?"), obj.get("z", "?")
                    tag = obj.get("tag", "")
                    tag_str = f"  [{tag}]" if tag and tag not in ("Untagged", "") else ""
                    lines.append(f"  {name!r}  dist={dist}m  pos=({x}, {y}, {z}){tag_str}")

        # Screen message
        msg = s.get("screen_message", "")
        if msg:
            lines.append(f"Screen message: {msg!r}")

        # Inventory
        inv = s.get("inventory", {})
        pouch = inv.get("pouch", [])
        equipped = inv.get("equipped", {})
        if pouch:
            item_strs = []
            for i in pouch[:12]:
                qty = i.get("quantity", 1)
                name = i.get("name", "?")
                item_strs.append(f"{name}x{qty}" if qty > 1 else name)
            lines.append(f"Pouch ({len(pouch)} items): {', '.join(item_strs)}")
        if equipped:
            worn = [f"{slot}={name}" for slot, name in equipped.items() if name]
            if worn:
                lines.append(f"Equipped: {', '.join(worn)}")

        return "\n".join(l for l in lines if l)
