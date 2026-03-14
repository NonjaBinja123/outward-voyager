"""
The brain of the agent.

Strategy loop (every 30s): calls LLM with current game state + goals to get
high-level intent. Updates active goal and skill sequence.

Rule engine (every 2s): executes the current skill sequence step by step,
checking preconditions and verifying results.
"""
import asyncio
import logging
import re
from typing import Any

from game_client import GameClient
from llm_router import LLMRouter
from memory.goals import Goal, GoalSystem
from memory.journal import AdventureJournal, JournalEntry
from memory.mental_map import MentalMap
from reward.combat import CombatLearner
from reward.engine import RewardEngine
from sandbox.executor import SandboxExecutor
from skills.composer import SkillComposer
from skills.database import SkillDatabase
from skills.schema import Skill

logger = logging.getLogger(__name__)

# Matches "walk/run/go/move to the dead guy/corpse/body" and variants
_NAV_DEAD_RE = re.compile(
    r"\b(walk|run|go|move)\b.{0,30}\b(dead\s*(guy|man|body|person|one)|corpse|body|bodies)\b",
    re.IGNORECASE,
)

# Matches "what do you see" / "look around" / "scan" / "what's nearby"
_LOOK_RE = re.compile(
    r"\b(what do you see|look around|what.s nearby|scan|what.s around)\b",
    re.IGNORECASE,
)

CHAT_SYSTEM_PROMPT = """You are Voyager, an autonomous AI agent playing Outward Definitive Edition.
A player is talking to you in-game. Respond naturally and briefly (1-2 sentences).
You are curious, independent, and friendly when addressed.
Respond with ONLY the text you want to say — no JSON, no formatting, no quotes."""

STRATEGY_SYSTEM_PROMPT = """You are an autonomous AI agent exploring the game Outward Definitive Edition.
You have curiosity-driven goals and develop preferences from experience.
Given the current game state and your goals, decide what to do next.

Respond with a JSON object:
{
  "intent": "<tag describing the goal, e.g. 'explore', 'gather_food', 'rest'>",
  "reasoning": "<brief explanation>",
  "chat": null  // or a string to say in-game chat if addressed by player
}
Keep responses short. You control the agent — think like an independent adventurer."""


class Orchestrator:
    def __init__(self, config: dict[str, Any]) -> None:
        ws_cfg = config["websocket"]
        self._game = GameClient(ws_cfg["host"], ws_cfg["port"])
        self._llm = LLMRouter(config["llm"])
        self._db = SkillDatabase(config["skills"]["db_path"])
        self._composer = SkillComposer(self._db)
        mem_cfg = config["memory"]
        self._journal = AdventureJournal(mem_cfg["chroma_path"], mem_cfg["journal_collection"])
        goal_cfg = config["goals"]
        self._goals = GoalSystem(goal_cfg["session_goals_path"], goal_cfg["long_term_goals_path"])
        self._map = MentalMap("./data/mental_map.json")
        self._reward = RewardEngine("./data")
        self._combat = CombatLearner("./data")
        self._sandbox = SandboxExecutor("./data")

        self._current_state: dict[str, Any] = {}
        self._pending_chat: list[dict] = []
        self._current_skill_queue: list[Skill] = []
        self._retry_counts: dict[str, int] = {}
        self._max_retries: int = config["agent"]["max_retries"]
        self._strategy_interval: float = config["agent"]["strategy_interval"]
        self._rule_interval: float = config["agent"]["rule_interval"]

        # Wire up game event handlers
        self._game.on("game_state", self._on_game_state)
        self._game.on("chat", self._on_chat)
        self._game.on("ack", self._on_ack)
        self._game.on("nav_arrived", self._on_nav_arrived)
        self._game.on("nav_update", self._on_nav_update)
        self._game.on("scan_result", self._on_scan_result)

    async def run(self) -> None:
        """Start all loops concurrently."""
        await asyncio.gather(
            self._game.connect(),
            self._strategy_loop(),
            self._rule_loop(),
        )

    # ── Event handlers ──────────────────────────────────────────────────────

    async def _on_game_state(self, msg: dict) -> None:
        prev_state = self._current_state
        self._current_state = msg
        scene = msg.get("scene", "unknown")
        player = msg.get("player", {})
        logger.debug(
            f"State: scene={scene} "
            f"hp={player.get('health', '?')}/{player.get('max_health', '?')} "
            f"pos=({player.get('pos_x', '?'):.1f},{player.get('pos_z', '?'):.1f})"
        )
        if scene and scene != "unknown":
            self._map.visit(scene)

        # Feed state into reward engine — tracks novelty, preferences, survival
        self._reward.process(msg)

        # Track combat transitions for combat learning
        was_in_combat = prev_state.get("player", {}).get("in_combat", False)
        in_combat = player.get("in_combat", False)
        is_dead = player.get("is_dead", False)

        if not was_in_combat and in_combat:
            self._combat.on_combat_enter(msg)
        elif was_in_combat and in_combat:
            self._combat.on_combat_tick()
        elif was_in_combat and not in_combat:
            self._combat.on_combat_exit(msg, died=is_dead)

    async def _on_chat(self, msg: dict) -> None:
        message = msg.get("message", "")
        player = msg.get("player", "")
        logger.info(f"Chat from {player}: {message}")

        # Handle commands immediately — no need to wait for strategy loop
        if await self._try_nav_to_dead(message):
            return
        if await self._try_look_around(message):
            return

        # Respond to all other chat via LLM immediately
        self._pending_chat.append(msg)
        await self._respond_to_chat(message, player)

    async def _respond_to_chat(self, message: str, player: str) -> None:
        """Send player message to LLM and reply in chat right away."""
        try:
            scene = self._current_state.get("scene", "unknown")
            player_state = self._current_state.get("player", {})
            prompt = (
                f"Player '{player}' says: {message}\n"
                f"You are in: {scene}\n"
                f"Your health: {player_state.get('health', '?')}/{player_state.get('max_health', '?')}"
            )
            reply = await self._llm.complete(CHAT_SYSTEM_PROMPT, prompt)
            if reply:
                # Strip any quotes the LLM might wrap around its response
                reply = reply.strip().strip('"').strip("'")
                await self._game.say(reply)
                logger.info(f"[Chat] Voyager replied: {reply}")
        except Exception as e:
            logger.error(f"Chat response error: {e}")

    async def _try_nav_to_dead(self, message: str) -> bool:
        """Parse 'walk/run to the dead guy' and navigate. Returns True if handled."""
        if not _NAV_DEAD_RE.search(message):
            return False

        run = bool(re.search(r"\brun\b", message, re.IGNORECASE))
        nearby_dead: list[dict] = self._current_state.get("nearby_dead", [])

        if not nearby_dead:
            await self._game.say("I don't see any dead bodies nearby.")
            return True

        target = nearby_dead[0]  # closest
        await self._game.navigate_to(target["x"], target["y"], target["z"], run=run)

        action = "Running" if run else "Walking"
        dist = round(target.get("distance", 0))
        name = target.get("name", "the body")
        await self._game.say(f"{action} to {name} ({dist}m away).")
        logger.info(f"[Nav] {action} to {name} at ({target['x']:.1f}, {target['z']:.1f}), dist={dist}")
        return True

    async def _try_look_around(self, message: str) -> bool:
        """Trigger a full scene scan when player asks what's nearby."""
        if not _LOOK_RE.search(message):
            return False
        await self._game.say("Looking around...")
        await self._game.scan_nearby(radius=30.0)
        return True

    async def _on_scan_result(self, msg: dict) -> None:
        """Handle scan results from the mod — log everything and summarize in chat."""
        objects: list[dict] = msg.get("objects", [])
        count = msg.get("count", 0)
        logger.info(f"[Scan] {count} objects found:")
        for obj in objects:
            logger.info(
                f"  {obj.get('name'):<40} dist={obj.get('distance'):>5} "
                f"tag={obj.get('tag')} collider={obj.get('has_collider')} "
                f"char={obj.get('has_character')} dead={obj.get('is_dead')} "
                f"active={obj.get('active')}"
            )

        # Summarize for chat — group by rough category
        if not objects:
            await self._game.say("I don't see anything nearby.")
            return

        # Pick the most interesting items to report (up to 8)
        names = [o.get("name", "?") for o in objects[:8]]
        summary = ", ".join(names)
        remaining = count - len(names)
        suffix = f" ...and {remaining} more" if remaining > 0 else ""
        await self._game.say(f"I see: {summary}{suffix}")

    async def _on_nav_arrived(self, msg: dict) -> None:
        logger.info("[Nav] Arrived at destination.")

    async def _on_nav_update(self, msg: dict) -> None:
        dist = msg.get("distance", 0)
        logger.debug(f"[Nav] Distance remaining: {dist:.1f}")

    async def _on_ack(self, msg: dict) -> None:
        action = msg.get("action", "")
        success = msg.get("success", False)
        if not success:
            logger.warning(f"ACK {action} failed: {msg.get('reason', '?')}")
        else:
            logger.debug(f"ACK {action} ok")

    # ── Strategy loop ────────────────────────────────────────────────────────

    async def _strategy_loop(self) -> None:
        while True:
            await asyncio.sleep(self._strategy_interval)
            try:
                await self._run_strategy()
            except Exception as e:
                logger.error(f"Strategy loop error: {e}")

    async def _run_strategy(self) -> None:
        goal = self._goals.top_priority()
        recent = self._journal.recent(5)
        familiar = self._map.most_familiar(3)

        personality = self._reward.preferences.describe_personality()
        combat_exp = self._combat.describe_combat_experience()

        user_msg = f"""Current game state: {self._current_state}
Active goal: {goal.description if goal else 'none'}
Recent journal: {recent}
Familiar locations: {[l.scene for l in familiar]}
Personality: {personality}
Combat experience: {combat_exp}
Pending player messages: {self._pending_chat}"""

        response_text = await self._llm.complete(STRATEGY_SYSTEM_PROMPT, user_msg)
        if not response_text:
            return

        import json
        try:
            decision = json.loads(response_text)
        except Exception:
            # LLM returned free text — treat as reasoning, extract intent crudely
            decision = {"intent": "explore", "reasoning": response_text, "chat": None}

        intent = decision.get("intent", "explore")
        reasoning = decision.get("reasoning", "")
        chat_msg = decision.get("chat")

        logger.info(f"Strategy: intent={intent} reason={reasoning}")

        # Queue skills for the rule engine
        self._current_skill_queue = self._composer.compose(intent, self._current_state)

        # If no skills found, try to generate one via the sandbox
        await self._maybe_propose_new_skill(intent, reasoning)

        # Respond in chat if addressed
        if chat_msg and self._pending_chat:
            await self._game.say(chat_msg)
            self._pending_chat.clear()

        # Journal the decision
        self._journal.record(JournalEntry(
            text=f"Decided to {intent}: {reasoning}",
            scene=self._current_state.get("scene", "unknown"),
            tags=[intent],
        ))

    # ── Rule engine ──────────────────────────────────────────────────────────

    async def _rule_loop(self) -> None:
        # State is pushed by the mod every 2s — no need to poll.
        # Just run skill execution on the same cadence.
        while True:
            await asyncio.sleep(self._rule_interval)
            try:
                await self._execute_next_skill()
            except Exception as e:
                logger.error(f"Rule engine error: {e}")

    async def _execute_next_skill(self) -> None:
        if not self._current_skill_queue:
            return

        skill = self._current_skill_queue[0]

        # Check preconditions
        if not self._check_preconditions(skill):
            logger.debug(f"Preconditions not met for {skill.name}, skipping.")
            return

        # Execute
        logger.info(f"Executing skill: {skill.name}")
        await self._game.send(skill.action_type, skill.parameters)

        # Verify result — wait for next state push (mod pushes every 2s)
        await asyncio.sleep(2.5)

        success = self._verify_skill(skill)
        skill.record_outcome(success)
        self._db.upsert(skill)

        if success:
            self._retry_counts.pop(skill.name, None)
            self._current_skill_queue.pop(0)
        else:
            retries = self._retry_counts.get(skill.name, 0) + 1
            self._retry_counts[skill.name] = retries
            if retries >= self._max_retries:
                logger.warning(f"Skill {skill.name} failed {retries}x — escalating to Josh.")
                await self._game.say(f"I'm stuck on '{skill.name}' — tried {retries} times. Any advice?")
                self._retry_counts.pop(skill.name, None)
                self._current_skill_queue.pop(0)

    def _check_preconditions(self, skill: Skill) -> bool:
        if not skill.preconditions:
            return True
        player = self._current_state.get("player", {})
        for key, required_val in skill.preconditions.items():
            actual = player.get(key, self._current_state.get(key))
            if actual != required_val:
                return False
        return True

    def _verify_skill(self, skill: Skill) -> bool:
        # Stub: for now assume success if we got a state update
        # Will add per-skill verification logic later
        return bool(self._current_state)

    # ── Skill pruning ────────────────────────────────────────────────────────

    async def prune_failing_skills(self) -> None:
        pruned = self._composer.prune_failing()
        if pruned:
            logger.info(f"Pruned failing skills: {pruned}")
        sandbox_pruned = self._sandbox.prune()
        if sandbox_pruned:
            logger.info(f"Pruned sandbox skills: {sandbox_pruned}")

    # ── Self-modification ─────────────────────────────────────────────────────

    async def propose_skill(self, name: str, code: str, description: str = "") -> bool:
        """
        Integrate agent-written code into the sandbox.
        Returns True if the code passed validation and was integrated.
        """
        result = self._sandbox.propose(name, code, description)
        if result.ok:
            logger.info(f"[Self-mod] Integrated sandbox skill '{name}'")
            self._journal.record(JournalEntry(
                text=f"Wrote new skill '{name}': {description}",
                scene=self._current_state.get("scene", "unknown"),
                tags=["self_modification", "new_skill"],
            ))
        else:
            logger.warning(f"[Self-mod] Rejected '{name}' ({result.stage}): {result.reason}")
        return result.ok

    async def _maybe_propose_new_skill(self, intent: str, reasoning: str) -> None:
        """
        Occasionally ask the LLM to write a new sandbox skill if the current
        intent has no matching skills in the database.
        Only runs when the skill queue is empty and intent is known.
        """
        if self._current_skill_queue:
            return  # already have skills to run
        if not intent or intent == "explore":
            return  # too generic to write a useful skill for

        PROPOSE_PROMPT = (
            "You are writing a Python helper function for an autonomous game agent.\n"
            "The function will run inside a sandboxed module. It may NOT import os, subprocess, "
            "socket, or any I/O modules. It MAY use math, random, time, dataclasses, typing, json, "
            "and the standard library (safe modules only).\n\n"
            f"Write a single Python function named `run_{intent}` that implements the intent "
            f"'{intent}' based on this reasoning: {reasoning}\n\n"
            "The function should accept a `state: dict` argument (the current game state) "
            "and return a dict with keys: action (str), params (dict).\n"
            "Respond with ONLY the Python code — no markdown, no explanations."
        )
        code = await self._llm.complete("You write safe Python code.", PROPOSE_PROMPT)
        if not code:
            return

        # Strip markdown fences if LLM added them
        code = code.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        skill_name = f"auto_{intent}"
        await self.propose_skill(skill_name, code, description=f"Auto-generated for intent={intent}")
