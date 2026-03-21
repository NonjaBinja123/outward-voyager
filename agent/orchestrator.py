"""
Orchestrator — slim event-driven wiring.

Game events → EventBus → Brain → ActionDispatcher → game.

No game knowledge. No decisions. Pure plumbing.
"""
import asyncio
import json
import logging
import time
from collections import deque
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

        self._connected: bool = False
        self._first_connect: bool = True   # fire strategy only on the very first connect
        self._last_scan_time: float = 0.0
        self._watcher_task: asyncio.Task | None = None  # only one watcher at a time
        # Pending player chat messages waiting to be included in next think()
        self._pending_chat: list[str] = []
        # Last N dispatched actions — shown to LLM so it doesn't repeat itself
        self._recent_actions: deque[str] = deque(maxlen=5)
        # Track how many times each UID has been attempted via trigger_interaction.
        # Cleared on scene change. UIDs tried 3+ times are shown as "stuck" to the LLM.
        self._interaction_attempts: dict[str, int] = {}
        # Track recently visited nav targets (snapped to 5-unit grid) with timestamps.
        # 5-unit grid means (-177,781) and (-178,782) are the same cell — prevents
        # the agent from retrying the same impassable area with slightly different coords.
        self._visited_nav: dict[tuple[int, int], float] = {}
        self._NAV_REVISIT_COOLDOWN = 90.0  # seconds before re-navigating to same spot
        # Consecutive nav failures at the same player position — triggers forced exploration
        self._stuck_position_count: int = 0
        self._last_stuck_cell: tuple[int, int] = (0, 0)

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

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _nav_cell(x: float, z: float) -> tuple[int, int]:
        """Snap coordinates to a 10-unit grid for visited-nav deduplication.
        10 units = 10m cells; anything within 10m of a failed target is treated as blocked."""
        return (round(x / 10) * 10, round(z / 10) * 10)

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
        self._connected = True
        self._loading_screen_active = False
        await self._game.set_autonomous(self._autonomous)
        await self._game.read_skills()
        await self._scan_scene()
        # Fire strategy only on the very first connect of this session.
        # After that, scene_changed events drive new-area thinking.
        # Reconnects from scene transitions do NOT fire strategy — prevents spam.
        if self._first_connect:
            self._first_connect = False
            async def _delayed_start():
                await asyncio.sleep(3.0)
                if self._connected and not self._loading_screen_active:
                    self._bus.on_strategy_request("just connected")
            self._connect_task = asyncio.create_task(_delayed_start())

    async def _on_disconnected(self, _msg: dict) -> None:
        self._connected = False
        self._loading_screen_active = True
        if hasattr(self, '_connect_task') and not self._connect_task.done():
            self._connect_task.cancel()
        # Only start one watcher at a time
        if self._watcher_task is None or self._watcher_task.done():
            self._watcher_task = asyncio.create_task(self._loading_screen_watcher())
        # Also watch for any action prompts on the transition screen
        await self._start_prompt_watcher()

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
        # Clear interaction attempt counts when entering a new scene
        if delta.scene_changed:
            self._interaction_attempts.clear()
            self._visited_nav.clear()
            self._stuck_position_count = 0
        # Navigation stuck check
        is_stuck, px, py, pz = self._state.check_stuck()
        if is_stuck:
            # Blacklist both current position and the nav target (10-unit grid)
            self._visited_nav[self._nav_cell(px, pz)] = time.time()
            tx, tz = self._state.nav_target
            self._visited_nav[self._nav_cell(tx, tz)] = time.time()
            # Count consecutive stucks near the same player position
            current_cell = self._nav_cell(px, pz)
            if current_cell == self._last_stuck_cell:
                self._stuck_position_count += 1
            else:
                self._stuck_position_count = 1
                self._last_stuck_cell = current_cell
            # After 3 consecutive stucks at the same spot, inject random exploration offset
            # so the LLM has a concrete alternative instead of retrying blocked targets
            if self._stuck_position_count >= 3:
                import random
                self._stuck_position_count = 0
                # Pick a random direction (±30–60m) away from current position
                angle = random.uniform(0, 360)
                import math
                dist = random.uniform(30, 60)
                ex = px + math.cos(math.radians(angle)) * dist
                ez = pz + math.sin(math.radians(angle)) * dist
                logger.info(f"[Nav] Consecutive stuck — injecting exploration target ({ex:.0f}, {ez:.0f})")
                # Add to goals as a temporary target so LLM picks it up
                from memory.goals import Goal
                from uuid import uuid4
                self._goals.add_session_goal(Goal(
                    id=str(uuid4()),
                    description=f"Explore in a new direction: navigate_to ({ex:.1f}, {py:.1f}, {ez:.1f})"
                ))
            self._bus.on_nav_stuck(px, py, pz)

    async def _on_chat(self, msg: dict) -> None:
        # Mod sends: {"type": "chat", "player": uid, "message": text}
        speaker = msg.get("player", msg.get("player_name", ""))
        text = msg.get("message", msg.get("text", ""))
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
        # Record this position as recently visited (5-unit grid)
        p = self._state.player
        self._visited_nav[self._nav_cell(p.get("pos_x", 0), p.get("pos_z", 0))] = time.time()
        await self._scan_scene()
        self._bus.on_nav_arrived()

    async def _on_nav_failed(self, msg: dict) -> None:
        tx, tz = self._state.nav_target
        self._state.set_nav_failed()
        # Blacklist this target — don't retry the same unreachable area (5-unit grid)
        self._visited_nav[self._nav_cell(tx, tz)] = time.time()
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

    _SCAN_INTERVAL: float = 60.0  # seconds between automatic re-scans

    async def _on_event(self, event: GameEvent) -> None:
        """All LLM calls flow through here."""
        if not self._connected:
            logger.debug(f"[Orch] Skipping event {event.name!r} — game not connected")
            return
        # On death — start vision watcher to handle respawn/defeat screen prompts
        if event.name == "death":
            await self._start_prompt_watcher()
        # Refresh scene scan if stale — agent's view of the world needs to move with him
        if time.time() - self._last_scan_time > self._SCAN_INTERVAL:
            await self._scan_scene()
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
            # Record for context on next think() — LLM sees what it just tried
            for a in actions[:3]:  # record first 3 steps only
                name = a.get("action", "")
                params = a.get("params", {})
                self._recent_actions.append(f"{name}({params})")
                # Track trigger_interaction attempts per UID
                if name == "trigger_interaction":
                    uid = params.get("uid", "")
                    if uid:
                        self._interaction_attempts[uid] = self._interaction_attempts.get(uid, 0) + 1
                # Track navigate_to targets dispatched (5-unit grid)
                if name == "navigate_to":
                    self._visited_nav[self._nav_cell(
                        float(params.get("x", 0)), float(params.get("z", 0))
                    )] = time.time()

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
        active_goals = [g.description for g in
                        (self._goals.active_session_goals() or self._goals.active_long_term_goals())[:5]]
        if not active_goals:
            active_goals = ["Explore the current area. Navigate to characters and objects nearby. Pick up any items you find."]
        recent_texts = self._journal.recent(5)
        extra_parts = []
        if self._recent_actions:
            extra_parts.append("RECENTLY ATTEMPTED: " + ", ".join(self._recent_actions))
        # Prune old visited nav entries
        now = time.time()
        self._visited_nav = {k: v for k, v in self._visited_nav.items()
                             if now - v < self._NAV_REVISIT_COOLDOWN}
        if self._visited_nav:
            visited_strs = [f"({x}±10, {z}±10)" for (x, z) in self._visited_nav]
            extra_parts.append(
                f"RECENTLY VISITED AREAS (do NOT navigate within 10m of these for ~{self._NAV_REVISIT_COOLDOWN:.0f}s): "
                + ", ".join(visited_strs[-8:])  # show last 8
            )
        # Active navigation
        if self._state.is_navigating:
            tx, tz = self._state.nav_target
            extra_parts.append(f"NAVIGATING to ({tx:.1f}, ?, {tz:.1f}) — use wait_for_arrival to block until done, or issue new navigate_to to redirect")
        # UIDs tried 3+ times with no state change — tell the LLM to stop
        stuck = [uid for uid, n in self._interaction_attempts.items() if n >= 3]
        if stuck:
            extra_parts.append(
                "STUCK INTERACTIONS (tried 3+ times, NO effect — do NOT attempt these again): "
                + ", ".join(stuck)
            )
        extra = "\n".join(extra_parts)
        blocked_cells = set(self._visited_nav.keys())
        stuck_uids = {uid for uid, n in self._interaction_attempts.items() if n >= 3}
        return Observation(
            state=self._state.current,
            recent_journal=recent_texts,
            active_goals=active_goals,
            pending_chat=list(self._pending_chat),
            scene_objects=self._state.nearby_objects,
            extra_context=extra,
            blocked_nav_cells=blocked_cells,
            stuck_uids=stuck_uids,
        )

    # ── Scene scanning ────────────────────────────────────────────────────────

    async def _scan_scene(self, radius: float = 80.0) -> None:
        """Issue a scan_nearby to refresh the agent's view of the surrounding area."""
        self._last_scan_time = time.time()
        await self._game.scan_nearby(radius=radius)

    # ── Screen reading ────────────────────────────────────────────────────────

    async def _loading_screen_watcher(self) -> None:
        """Fire vision reads only while the game is actually disconnected (loading screen)."""
        logger.info("[Screen] Scene transition — watching for loading screen tips")
        attempts = 0
        while self._loading_screen_active and not self._connected and attempts < 12:
            await self._read_screen()
            await asyncio.sleep(10.0)
            attempts += 1
        logger.info("[Screen] Loading screen watcher stopped")

    _prompt_watcher_task: "asyncio.Task | None" = None

    async def _start_prompt_watcher(self) -> None:
        """Start a vision loop that presses required keys on death/transition screens."""
        if self._prompt_watcher_task and not self._prompt_watcher_task.done():
            return
        self._prompt_watcher_task = asyncio.create_task(self._prompt_screen_watcher())

    async def _prompt_screen_watcher(self) -> None:
        """Poll screenshots while dead or on a transition screen, pressing required keys."""
        logger.info("[Screen] Prompt watcher started — watching for death/transition prompts")
        for _ in range(20):  # max 20 attempts (~60s)
            await asyncio.sleep(3.0)
            try:
                data = await self._screen_reader.read_screen(min_interval=0)
                if not data:
                    continue
                # Store any new tips
                for tip in self._screen_reader.new_tips(data):
                    logger.info(f"[Screen] Tip: {tip}")
                    self._journal.record(JournalEntry(
                        scene=self._state.scene or "transition",
                        text=tip, tags=["game_tip"],
                    ))
                # Press the required key if a death/prompt screen is detected
                if data.get("action_required") and data.get("required_key"):
                    key = data["required_key"]
                    logger.info(f"[Screen] Action required — pressing {key!r}")
                    await self._game.press_key(key)
                    await asyncio.sleep(1.5)
                    continue  # keep watching in case more presses needed
                # Stop if we're back in normal gameplay (connected, not dead)
                player = self._state.player
                if self._connected and not player.get("is_dead") and not data.get("is_death_screen"):
                    logger.info("[Screen] Prompt watcher — gameplay resumed")
                    return
            except Exception as e:
                logger.warning(f"[Screen] Prompt watcher error: {e}")
        logger.info("[Screen] Prompt watcher stopped")

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
            if data.get("all_text"):
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
