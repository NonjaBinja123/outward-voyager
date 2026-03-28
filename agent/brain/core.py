"""
Core — the Brain class. Orchestrates prompts → LLM → parser → action plan.

Responsible for:
- Choosing reactive vs strategy tier based on event type
- Building the user prompt (event + observation bundle)
- Calling the LLM router
- Returning the parsed plan

Imports from sub-modules so each piece can be tested independently.
"""
import asyncio
import json
import logging
from typing import Any

from event_bus import GameEvent
from brain.observation import Observation
from brain.prompts import build_system
from brain.parser import parse as parse_response

logger = logging.getLogger(__name__)


class Brain:
    """
    Packages observations into LLM prompts, calls the right tier, returns action plan.

    Tiers:
      - reactive: fast vision model — moment-to-moment decisions with screenshot
      - strategy: deep text model — reflection + goal-setting (death, strategy_request)
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
        obs: Observation,
        screen_description: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Given a triggering event and observation bundle, call the LLM and return
        a parsed action plan dict, or None on failure.

        screen_description: text summary produced by the vision model reading the
                            current screenshot. Injected into the user prompt so
                            the text decision model knows what's on screen.
                            Always uses complete() — vision model only reads,
                            text model decides.
        """
        use_strategy = event.name in self.STRATEGY_EVENTS
        system = build_system(self._registry, strategy=use_strategy)
        user = self._build_user(event, obs, screen_description=screen_description)
        task = "strategy" if use_strategy else "reactive"
        max_tokens = 6144 if use_strategy else 4096

        logger.info(
            f"[Brain] Think — event={event.name!r} tier={task} "
            f"screen={'yes' if screen_description else 'no'}"
        )
        try:
            raw = await self._llm.complete(system, user, task=task, max_tokens=max_tokens)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[Brain] LLM call failed: {e}")
            return None

        return parse_response(raw)

    # ── User prompt builder ───────────────────────────────────────────────────

    def _build_user(self, event: GameEvent, obs: Observation, screen_description: str | None = None) -> str:
        """
        Build the user-turn message: event type + full game state observation.
        This is what changes every LLM call; the system prompt stays constant.
        """
        lines = [f"EVENT: {event.name}"]
        if event.data:
            lines.append(f"EVENT DATA: {json.dumps(event.data)}")

        # For chat events, highlight the message prominently
        if event.name in ("player_chat", "dashboard_chat") and obs.pending_chat:
            speaker = event.data.get("speaker", "Player")
            text = event.data.get("text", "")
            lines.append(f"IMPORTANT: {speaker} said: \"{text}\"")
            lines.append(
                "If you are not in combat or actively navigating, include a say action "
                "to reply naturally. If busy, prioritize the game action."
            )

        if screen_description:
            lines.append("")
            lines.append(f"CURRENT SCREEN: {screen_description}")

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
            lines.append("  -> If not in combat or immediate danger, a brief say reply is natural. Your call.")

        if obs.extra_context:
            lines.append("")
            lines.append("ADDITIONAL CONTEXT:")
            lines.append(obs.extra_context)

        return "\n".join(lines)
