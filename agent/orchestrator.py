"""
The brain of the agent.

Strategy loop (every 30s): calls LLM with current game state + goals to get
high-level intent. Updates active goal and skill sequence.

Rule engine (every 2s): executes the current skill sequence step by step,
checking preconditions and verifying results.
"""
import asyncio
import logging
from typing import Any

from game_client import GameClient
from llm_router import LLMRouter
from memory.goals import Goal, GoalSystem
from memory.journal import AdventureJournal, JournalEntry
from memory.mental_map import MentalMap
from skills.composer import SkillComposer
from skills.database import SkillDatabase
from skills.schema import Skill

logger = logging.getLogger(__name__)

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

    async def run(self) -> None:
        """Start all loops concurrently."""
        await asyncio.gather(
            self._game.connect(),
            self._strategy_loop(),
            self._rule_loop(),
        )

    # ── Event handlers ──────────────────────────────────────────────────────

    async def _on_game_state(self, msg: dict) -> None:
        self._current_state = msg
        scene = msg.get("scene", "unknown")
        player = msg.get("player", {})
        logger.info(
            f"State: scene={scene} "
            f"hp={player.get('health', '?')}/{player.get('max_health', '?')} "
            f"pos=({player.get('pos_x', '?'):.1f},{player.get('pos_z', '?'):.1f})"
        )
        if scene and scene != "unknown":
            self._map.visit(scene)

    async def _on_chat(self, msg: dict) -> None:
        logger.info(f"Chat from {msg.get('player')}: {msg.get('message')}")
        self._pending_chat.append(msg)

    async def _on_ack(self, msg: dict) -> None:
        action = msg.get("action", "")
        success = msg.get("success", False)
        logger.debug(f"ACK {action} success={success}")

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

        user_msg = f"""Current game state: {self._current_state}
Active goal: {goal.description if goal else 'none'}
Recent journal: {recent}
Familiar locations: {[l.scene for l in familiar]}
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
