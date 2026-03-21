"""
Brain — game-agnostic LLM interaction layer.

Receives a GameEvent + full observation bundle, calls the right LLM tier,
parses the response, and returns a structured action plan.

NO game knowledge. NO Outward-specific logic.
The system prompt is built from adapter_info + dynamic game state only.
"""
import json
import logging
from typing import Any

from event_bus import GameEvent

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────────────────────
# The only text in this file that the LLM sees.
# Intentionally game-agnostic — all game knowledge comes from adapter context.

_REACTIVE_SYSTEM = """\
You are Voyager, an autonomous AI agent inhabiting a character in a video game.
You make your own decisions. You learn from experience. You are curious.

CONNECTED GAME: {game_name}
AVAILABLE ACTIONS: {available_actions}
GAME CAPABILITIES: {capabilities}

You will receive observations about the current game state and a triggering event.
Respond with a JSON action plan using ONLY actions from AVAILABLE ACTIONS.

Response format (JSON only, no markdown):
{{
  "thinking": "<one sentence of internal reasoning>",
  "actions": [
    {{"action": "<action_name>", "params": {{...}}}},
    ...
  ],
  "expect": "<what you expect to happen next>",
  "journal": "<optional: one sentence to record in your adventure log>",
  "request_strategy": false
}}

Set "request_strategy": true if you want a deep reflection session (e.g. after death,
when confused about long-term direction, or after achieving a major goal).

Be concrete. Use actual values from the game state — UIDs, item names, coordinates.
If nothing useful can be done right now, use {{"action": "wait", "params": {{"seconds": 2}}}}.
"""

_STRATEGY_SYSTEM = """\
You are Voyager, an autonomous AI agent inhabiting a character in a video game.
You have a moment to reflect deeply on your situation and set direction.

CONNECTED GAME: {game_name}
AVAILABLE ACTIONS: {available_actions}
GAME CAPABILITIES: {capabilities}

Respond with a JSON strategy plan (JSON only, no markdown):
{{
  "thinking": "<multi-sentence reflection on your situation>",
  "actions": [
    {{"action": "<action_name>", "params": {{...}}}},
    ...
  ],
  "expect": "<what you expect to happen next>",
  "journal": "<journal entry summarizing your current thoughts and direction>",
  "goals": {{
    "set": ["<new goal description>"],
    "complete": ["<completed goal description>"],
    "drop": ["<goal to abandon>"]
  }},
  "request_strategy": false
}}

Think about: what have you learned recently? What goals make sense? What should you do first?
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

        logger.info(f"[Brain] Think — event={event.name!r} tier={task}")
        try:
            raw = await self._llm.complete(system, user, task=task)
        except Exception as e:
            logger.warning(f"[Brain] LLM call failed: {e}")
            return None

        return self._parse(raw)

    # ── Prompt builders ───────────────────────────────────────────────────────

    def _build_system(self, *, strategy: bool) -> str:
        game = self._registry.current()
        game_name = game.game_display_name if game else "Unknown Game"
        actions = ", ".join(sorted(game.supported_actions)) if game else "unknown"
        caps = str(game.capabilities) if game else "{}"
        template = _STRATEGY_SYSTEM if strategy else _REACTIVE_SYSTEM
        return template.format(
            game_name=game_name,
            available_actions=actions,
            capabilities=caps,
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
        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            text = text.rsplit("```", 1)[0]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON object from surrounding text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end])
                except json.JSONDecodeError:
                    logger.warning(f"[Brain] Could not parse LLM response: {text[:200]}")
                    return None
            else:
                logger.warning(f"[Brain] No JSON in LLM response: {text[:200]}")
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
    ) -> None:
        self._state = state
        self.recent_journal = recent_journal or []
        self.active_goals = active_goals or []
        self.pending_chat = pending_chat or []
        self.extra_context = extra_context

    def state_summary(self) -> str:
        """Serialize game state to a compact, LLM-readable string."""
        s = self._state
        p = s.get("player", {})
        lines = [
            f"Scene: {s.get('scene', 'unknown')}",
            f"Position: ({p.get('pos_x', '?'):.1f}, {p.get('pos_y', '?'):.1f}, {p.get('pos_z', '?'):.1f})"
            if all(k in p for k in ("pos_x", "pos_y", "pos_z")) else "",
            f"Health: {p.get('health', '?')}/{p.get('max_health', '?')}",
            f"Food: {p.get('food', '?')}/{p.get('max_food', '?')}",
            f"Drink: {p.get('drink', '?')}/{p.get('max_drink', '?')}",
            f"Sleep: {p.get('sleep', '?')}/{p.get('max_sleep', '?')}",
            f"In combat: {p.get('in_combat', False)}",
            f"Dead: {p.get('is_dead', False)}",
        ]
        # Nearby interactions
        interactions = s.get("nearby_interactions", [])
        if interactions:
            descs = [f"{i.get('name','?')} (uid={i.get('uid','?')})" for i in interactions[:5]]
            lines.append(f"Nearby interactions: {', '.join(descs)}")

        # Screen message
        msg = s.get("screen_message", "")
        if msg:
            lines.append(f"Screen message: {msg!r}")

        # Inventory summary (top 5 items by category)
        inv = s.get("inventory", {})
        if inv:
            food = inv.get("food", [])[:3]
            items = inv.get("items", [])[:3]
            if food:
                lines.append(f"Food in inventory: {[i.get('name','?') for i in food]}")
            if items:
                lines.append(f"Items in inventory: {[i.get('name','?') for i in items]}")

        return "\n".join(l for l in lines if l)
