"""
Orchestrator — slim event-driven wiring.

Game events → EventBus → Brain → ActionDispatcher → game.

No game knowledge. No decisions. Pure plumbing.
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from action_dispatcher import ActionDispatcher
from brain import Brain, Observation
from event_bus import EventBus, GameEvent
from game_client import GameClient
from keybinding_learner import KeybindingLearner
from llm_router import LLMRouter
from memory.goals import Goal, GoalSystem
from memory.journal import AdventureJournal, JournalEntry
from memory.mental_map import MentalMap
from protocol.adapter import AdapterInfo
from protocol.registry import AdapterRegistry
from screen_reader import ScreenReader
from state_manager import StateManager

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, config: dict[str, Any]) -> None:
        # ── Infrastructure ────────────────────────────────────────────────
        ws_cfg = config["websocket"]
        self._game = GameClient(ws_cfg["host"], ws_cfg["port"])
        self._llm = LLMRouter(config["llm"])

        # ── Memory ────────────────────────────────────────────────────────
        mem_cfg = config["memory"]
        self._journal = AdventureJournal(
            mem_cfg["chroma_path"], mem_cfg["journal_collection"]
        )
        goal_cfg = config["goals"]
        self._goals = GoalSystem(
            goal_cfg["session_goals_path"], goal_cfg["long_term_goals_path"]
        )
        self._map = MentalMap("./data/mental_map.json")

        # ── Protocol ──────────────────────────────────────────────────────
        self._registry = AdapterRegistry()

        # ── New arch modules ──────────────────────────────────────────────
        self._state = StateManager()
        self._bus = EventBus()
        self._brain = Brain(self._llm, self._registry)
        self._dispatcher = ActionDispatcher(self._game, self._state)

        # ── Vision / screen ───────────────────────────────────────────────
        self._screen_reader = ScreenReader(self._llm)
        self._keybindings = KeybindingLearner(
            "./data", llm_complete_vision=self._llm.complete_vision
        )
        self._loading_screen_active: bool = False

        # ── Config ────────────────────────────────────────────────────────
        agent_cfg = config.get("agent", {})
        self._autonomous: bool = agent_cfg.get("autonomous_movement", False)
        self._game_state_path = Path("./data/game_state.json")
        self._chat_log_path = Path("./data/chat_log.jsonl")
        self._chat_log_path.parent.mkdir(parents=True, exist_ok=True)

        # Pending player chat messages waiting to be included in next think()
        self._pending_chat: list[str] = []

        # ── Wire together ─────────────────────────────────────────────────
        self._state.on_delta(self._bus.on_state_delta)
        self._bus.on_event(self._on_event)
        self._dispatcher.on_completed(self._on_action_completed)
        self._dispatcher.on_failed(self._on_action_failed)

        self._game.on("connected",      self._on_connected)
        self._game.on("disconnected",   self._on_disconnected)
        self._game.on("game_state",     self._on_game_state)
        self._game.on("chat",           self._on_chat)
        self._game.on("ack",            self._on_ack)
        self._game.on("nav_arrived",    self._on_nav_arrived)
        self._game.on("nav_failed",     self._on_nav_failed)
        self._game.on("scan_result",    self._on_scan_result)
        self._game.on("skills",         self._on_skills)
        self._game.on("adapter_info",   self._on_adapter_info)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        self._bus.start()
        await asyncio.gather(
            self._game.connect(),
            self._poll_dashboard_chat(),
        )

    async def _poll_dashboard_chat(self) -> None:
        """Poll pending_dashboard_chat.json for messages from the dashboard UI."""
        path = Path("./data/pending_dashboard_chat.json")
        while True:
            await asyncio.sleep(1.0)
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                path.write_text("", encoding="utf-8")
                msgs = json.loads(text) if text.startswith("[") else [{"text": text}]
                for m in msgs:
                    msg_text = m.get("text", "")
                    if msg_text:
                        self._pending_chat.append(f"[dashboard] {msg_text}")
                        self._bus.on_dashboard_chat(msg_text)
            except Exception as e:
                logger.warning(f"[Dashboard] Chat poll error: {e}")

    # ── Game event handlers ───────────────────────────────────────────────────

    async def _on_connected(self, _msg: dict) -> None:
        self._loading_screen_active = False
        await self._game.set_autonomous(self._autonomous)
        await self._game.read_skills()
        await self._game.scan_nearby(radius=40.0)
        asyncio.create_task(self._read_screen())
        # Immediate first think — don't wait for idle_timeout
        self._bus.on_strategy_request("just connected")

    async def _on_disconnected(self, _msg: dict) -> None:
        self._loading_screen_active = True
        asyncio.create_task(self._loading_screen_watcher())

    async def _on_game_state(self, msg: dict) -> None:
        # Persist for dashboard
        try:
            self._game_state_path.write_text(json.dumps(msg, default=str), encoding="utf-8")
        except Exception:
            pass
        # Update mental map
        scene = msg.get("scene", "unknown")
        if scene and scene != "unknown":
            self._map.visit(scene)
        # Feed into StateManager — computes delta, fires delta handlers → EventBus
        delta = self._state.update(msg)
        # Navigation stuck check
        is_stuck, px, py, pz = self._state.check_stuck()
        if is_stuck:
            self._bus.on_nav_stuck(px, py, pz)

    async def _on_chat(self, msg: dict) -> None:
        speaker = msg.get("player_name", "")
        text = msg.get("text", "")
        if not text:
            return
        self._log_chat("player", text, speaker)
        self._pending_chat.append(f"{speaker}: {text}" if speaker else text)
        self._bus.on_player_chat(text, speaker)

    async def _on_ack(self, msg: dict) -> None:
        action = msg.get("action", "")
        success = msg.get("success", True)
        reason = msg.get("reason", "")
        if not success:
            self._bus.on_action_failed(action, reason)
            if action == "use_item":
                item = msg.get("item", "")
                if item:
                    self._journal.record(JournalEntry(
                        scene=self._state.scene,
                        text=f"Tried to use '{item}' but it had no effect. {reason}",
                        tags=["action_failed", "use_item"],
                    ))

    async def _on_nav_arrived(self, msg: dict) -> None:
        self._state.set_arrived()
        self._bus.on_nav_arrived()

    async def _on_nav_failed(self, msg: dict) -> None:
        self._state.set_nav_failed()
        self._bus.on_nav_failed(msg.get("reason", ""))

    async def _on_scan_result(self, msg: dict) -> None:
        objects = msg.get("objects", [])
        self._state.update_nearby_objects(objects)

    async def _on_skills(self, msg: dict) -> None:
        skills = msg.get("skills", [])
        self._state.update_known_skills(skills)

    async def _on_adapter_info(self, msg: dict) -> None:
        try:
            info = AdapterInfo.model_validate(msg)
            self._registry.register(info)
        except Exception as e:
            logger.warning(f"[Adapter] Could not parse adapter_info: {e}")

    # ── EventBus handler — the one place brain is called ─────────────────────

    async def _on_event(self, event: GameEvent) -> None:
        """All LLM calls flow through here."""
        obs = self._build_observation()
        result = await self._brain.think(event, obs)
        if not result:
            return

        # Journal entry
        journal_text = result.get("journal", "")
        if journal_text:
            self._journal.record(JournalEntry(
                scene=self._state.scene,
                text=journal_text,
                tags=["decision"],
            ))

        # Goal updates (strategy sessions only)
        goals_update = result.get("goals", {})
        if goals_update:
            for desc in goals_update.get("set", []):
                self._goals.add_session_goal(Goal(id=str(uuid4()), description=desc))
            for desc in goals_update.get("complete", []):
                # Match by description — mark first matching active goal complete
                for g in self._goals.session + self._goals.long_term:
                    if not g.completed and desc.lower() in g.description.lower():
                        self._goals.complete(g.id)
                        break

        # Dispatch actions
        actions = result.get("actions", [])
        if actions:
            self._dispatcher.dispatch(actions)

        # Agent self-requests strategy session
        if result.get("request_strategy"):
            self._bus.on_strategy_request("agent-requested")

        # Clear consumed chat
        self._pending_chat.clear()

    # ── Dispatcher callbacks ──────────────────────────────────────────────────

    async def _on_action_completed(self, action: str, result: dict | None) -> None:
        self._bus.on_action_completed(action, result)

    async def _on_action_failed(self, action: str, result: dict | None) -> None:
        self._bus.on_action_failed(action, result.get("error", "") if result else "")

    # ── Observation builder ───────────────────────────────────────────────────

    def _build_observation(self) -> Observation:
        goal = self._goals.top_priority()
        active_goals = [goal.description] if goal else []
        recent_texts = self._journal.recent(5)
        return Observation(
            state=self._state.current,
            recent_journal=recent_texts,
            active_goals=active_goals,
            pending_chat=list(self._pending_chat),
        )

    # ── Screen reading ────────────────────────────────────────────────────────

    async def _loading_screen_watcher(self) -> None:
        logger.info("[Screen] Scene transition — watching for loading screen tips")
        while self._loading_screen_active:
            await self._read_screen()
            await asyncio.sleep(2.0)

    async def _read_screen(self) -> None:
        try:
            data = await self._screen_reader.read_screen()
            if not data:
                return
            for tip in self._screen_reader.new_tips(data):
                logger.info(f"[Screen] New tip: {tip}")
                self._journal.record(JournalEntry(
                    scene="loading_screen",
                    text=tip,
                    tags=["game_tip"],
                ))
            hints = self._screen_reader.interaction_hints(data)
            for h in hints:
                self._keybindings.record_observation(h["action"], h["key"])
            # Surface hints to EventBus — LLM decides whether to press the key
            if hints or data.get("all_text"):
                self._bus.on_screen_read(data)
        except Exception as e:
            logger.warning(f"[Screen] read error: {e}")

    # ── Utilities ─────────────────────────────────────────────────────────────

    def _log_chat(self, role: str, message: str, name: str = "") -> None:
        entry: dict[str, Any] = {"timestamp": time.time(), "role": role, "message": message}
        if name:
            entry["name"] = name
        try:
            with self._chat_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"[Chat] Failed to write log: {e}")
