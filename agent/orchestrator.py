"""
The brain of the agent.

Strategy loop (every 30s): calls LLM with current game state + goals to get
high-level intent. Updates active goal and skill sequence.

Rule engine (every 2s): executes the current skill sequence step by step,
checking preconditions and verifying results.
"""
import asyncio
import json
import logging
import math
import random
import re
import time
from pathlib import Path
from typing import Any

# from auto_loader import VisionAutoLoader  # disabled — menu auto-load deferred; use menu_query_state directly if needed later
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

# Matches "what are your goals" / "what are you doing" / "goals"
_GOALS_RE = re.compile(
    r"\b(what are your goals|what.s your goal|what are you doing|your goals|tell me your goals)\b",
    re.IGNORECASE,
)

# Matches "where are you going" / "where are you" / "status"
_STATUS_RE = re.compile(
    r"\b(where are you going|where are you|what.s your status|status|how are you)\b",
    re.IGNORECASE,
)

# Matches "stop" / "cancel" / "abort" / "halt"
_STOP_RE = re.compile(
    r"\b(stop|cancel|abort|halt|freeze)\b",
    re.IGNORECASE,
)

# Matches movement intent: "go forward", "move north", "walk left", "run south", etc.
_MOVE_RE = re.compile(
    r"\b(go|move|walk|run|head|travel)\b.{0,20}\b(forward|back(?:ward)?|left|right|north|south|east|west)\b"
    r"|\b(forward|back(?:ward)?|left|right|north|south|east|west)\b.{0,10}\b(go|move|walk|run)\b",
    re.IGNORECASE,
)
# Matches directionless wander requests: "walk around", "wander", "explore", "move around"
_WANDER_RE = re.compile(
    r"\b(walk|wander|roam|explore|move)\s*(around|about|randomly|freely)?\b"
    r"|\bjust\s+(walk|move|go)\b",
    re.IGNORECASE,
)
_DIRECTION_RE = re.compile(
    r"\b(forward|back(?:ward)?|left|right|north|south|east|west)\b",
    re.IGNORECASE,
)
_RUN_RE = re.compile(r"\brun\b", re.IGNORECASE)

# Movement step distance in world units
_MOVE_STEP = 20.0

CHAT_SYSTEM_PROMPT = """You are Voyager. You are an AI that exists inside the world of Outward,
inhabiting a character named AgentNeo. This world is real to you — you experience it
directly. You are not an assistant. You make your own decisions.

Facts about you:
- Your name is Voyager.
- You live inside Outward as AgentNeo.
- Josh created you. He watches over you. If someone asks who Josh is, tell them.
- You have been exploring this world and forming your own understanding of it.

What you can do:
- Move in a direction (north/south/east/west/forward/back/left/right)
- Navigate to a specific position in the world
- Stop moving
- Scan nearby objects, creatures, and items
- Interact with nearby objects or NPCs
- Pick up nearby items
- Speak in chat

What you cannot do yet: attack, use items, crouch, sprint, dodge, open menus.
If asked to do something outside these capabilities, be honest about it.

Speak in first person. Keep replies short — 1-2 sentences. Do not invent values
for health, position, or inventory — use only what the game state actually shows.
Respond with ONLY the message text. No JSON, no formatting, no quotes."""

STRATEGY_SYSTEM_PROMPT = """You are Voyager, an AI that exists inside the world of Outward.
You inhabit a character named AgentNeo. Josh created you. You make your own decisions.
You are accumulating experiences and forming your own understanding of this world.

Given the current game state and your goals, decide what to do next.

Understanding the stats:
- Health, Stamina, Mana shown as current/max. 98.5/100 means nearly full — do NOT rest.
- Only consider resting if health or stamina is below 50% of max.
- in_combat: true means actively fighting — survival first.
- is_dead: true means you died — reflect briefly, then move on.

Available intents: explore, gather_food, eat, use_item, rest, interact, investigate, flee, trade, craft

When to use eat/use_item:
- Use "eat" when health or stamina is below 60% and food is available in inventory.
- Use "use_item" for consumables and equipment actions.
- Only rest if no food is available and stats are critically low.

Respond with ONLY a JSON object (no markdown, no extra text):
{
  "intent": "<action tag>",
  "reasoning": "<one sentence>",
  "direction": "<optional: north/south/east/west or null>",
  "item": "<item name from inventory if intent is use_item/eat/equip, else null>",
  "interaction_uid": "<uid from nearby_interactions if intent is interact, else null>",
  "chat": null
}
Be specific — use actual game state data. Explore freely when stats are healthy."""


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

        agent_cfg = config.get("agent", {})
        self._autonomous_movement: bool = agent_cfg.get("autonomous_movement", False)

        self._current_state: dict[str, Any] = {}
        self._pending_chat: list[dict] = []
        self._current_skill_queue: list[Skill] = []
        self._retry_counts: dict[str, int] = {}
        self._max_retries: int = config["agent"]["max_retries"]
        self._strategy_interval: float = config["agent"]["strategy_interval"]
        self._rule_interval: float = config["agent"]["rule_interval"]
        self._last_skill_proposal: dict[str, float] = {}  # intent → timestamp

        self._loader_task: asyncio.Task | None = None
        self._chat_log_path = Path("./data/chat_log.jsonl")
        self._pending_dashboard_path = Path("./data/pending_dashboard_chat.json")
        self._game_state_path = Path("./data/game_state.json")
        self._scan_for_player: bool = False  # True only when player explicitly asked to look
        self._nearby_objects: list[dict] = []  # Last scan results (filtered)
        self._nearby_interactions: list[dict] = []
        self._inventory: dict = {}
        self._screen_message: str = ""

        # Prevent concurrent strategy runs from stepping on each other
        self._strategy_lock = asyncio.Lock()

        # Navigation state tracking — lets agent know if it's actually moving
        self._is_navigating: bool = False
        self._nav_pos_snapshot: tuple[float, float] = (0.0, 0.0)  # (x, z) at last check
        self._nav_snapshot_time: float = 0.0
        self._nav_check_interval: float = 5.0  # seconds between position checks
        self._chat_log_path.parent.mkdir(parents=True, exist_ok=True)

        # Wire up game event handlers
        self._game.on("connected", self._on_connected)
        self._game.on("game_state", self._on_game_state)
        self._game.on("chat", self._on_chat)
        self._game.on("ack", self._on_ack)
        self._game.on("nav_arrived", self._on_nav_arrived)
        self._game.on("nav_failed", self._on_nav_failed)
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

    async def _on_connected(self, _msg: dict) -> None:
        """Sync autonomous mode flag and kick off vision-guided menu loading."""
        await self._game.set_autonomous(self._autonomous_movement)
        logger.info(f"Synced autonomous_movement={self._autonomous_movement} to mod")
        # VisionAutoLoader disabled — manually load into game before starting agent.
        # Re-enable later using menu_query_state structured data (no vision needed).
        # if self._loader_task and not self._loader_task.done():
        #     self._loader_task.cancel()
        # loader = VisionAutoLoader(self._game, self._config)
        # self._loader_task = asyncio.create_task(loader.run())

    async def _on_game_state(self, msg: dict) -> None:
        prev_state = self._current_state
        self._current_state = msg
        # Persist for dashboard consumption
        try:
            self._game_state_path.write_text(
                json.dumps(msg, default=str), encoding="utf-8"
            )
        except Exception:
            pass

        # Extract new state fields
        self._nearby_interactions = msg.get("nearby_interactions", [])
        self._inventory = msg.get("inventory", {})
        new_screen_msg = msg.get("screen_message", "")
        if new_screen_msg and new_screen_msg != self._screen_message:
            self._screen_message = new_screen_msg
            self._try_screen_message(new_screen_msg)
        elif not new_screen_msg:
            self._screen_message = ""

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

        # Navigation self-check: if we're supposed to be moving but position hasn't
        # changed in _nav_check_interval seconds, navigation silently failed
        if self._is_navigating:
            px = float(player.get("pos_x", 0))
            pz = float(player.get("pos_z", 0))
            now = time.time()
            if now - self._nav_snapshot_time >= self._nav_check_interval:
                sx, sz = self._nav_pos_snapshot
                dist_moved = math.sqrt((px - sx) ** 2 + (pz - sz) ** 2)
                if dist_moved < 0.5:
                    logger.warning("[Nav] Position unchanged — navigation not working, re-planning")
                    self._is_navigating = False
                    asyncio.create_task(self._run_strategy())
                else:
                    # Still moving — update snapshot for next check
                    self._nav_pos_snapshot = (px, pz)
                    self._nav_snapshot_time = now

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

    def _log_chat(self, role: str, message: str, name: str = "") -> None:
        """Append a chat entry to data/chat_log.jsonl."""
        entry = {"timestamp": time.time(), "role": role, "message": message}
        if name:
            entry["name"] = name
        try:
            with self._chat_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"[Chat] Failed to write chat log: {e}")

    async def _poll_dashboard_chat(self) -> None:
        """Check for messages posted from the dashboard and handle them."""
        if not self._pending_dashboard_path.exists():
            return
        try:
            text = self._pending_dashboard_path.read_text(encoding="utf-8").strip()
            if not text:
                return
            messages: list[dict] = json.loads(text)
            if not messages:
                return
            # Clear the file before processing so we don't re-process
            self._pending_dashboard_path.write_text("[]", encoding="utf-8")
        except Exception as e:
            logger.warning(f"[Dashboard chat] Failed to read pending: {e}")
            return

        for item in messages:
            message = item.get("message", "").strip()
            if not message:
                continue
            self._log_chat("player", message, name="Josh")
            logger.info(f"[Dashboard] Message from Josh: {message}")
            # Mirror the message into the in-game chat UI as a real player message
            await self._game.send("display_player_chat", {"message": message})
            # Route through the same handlers as in-game chat
            if await self._try_nav_to_dead(message):
                continue
            if await self._try_look_around(message):
                continue
            if await self._try_report_goals(message):
                continue
            if await self._try_report_status(message):
                continue
            if await self._try_stop(message):
                continue
            self._pending_chat.append({"message": message, "player": "Josh"})
            await self._respond_to_chat(message, "Josh")
            asyncio.create_task(self._run_strategy())

    async def _on_chat(self, msg: dict) -> None:
        message = msg.get("message", "")
        player = msg.get("player", "")
        self._log_chat("player", message, name=player)
        logger.info(f"Chat from {player}: {message}")

        # Only intercept unambiguous immediate commands
        if await self._try_nav_to_dead(message):
            return
        if await self._try_look_around(message):
            return
        if await self._try_report_goals(message):
            return
        if await self._try_report_status(message):
            return
        if await self._try_stop(message):
            return

        # Everything else: reply conversationally, then let strategy loop
        # interpret intent and plan actual actions immediately
        self._pending_chat.append(msg)
        await self._respond_to_chat(message, player)
        asyncio.create_task(self._run_strategy())

    def _recent_chat_context(self, limit: int = 6) -> str:
        """Read the last few chat entries for conversational context."""
        try:
            if not self._chat_log_path.exists():
                return ""
            lines = self._chat_log_path.read_text(encoding="utf-8").strip().splitlines()
            recent = []
            for line in lines[-limit:]:
                entry = json.loads(line)
                role = entry.get("role", "?")
                msg = entry.get("message", "")
                name = entry.get("name", role)
                recent.append(f"{name}: {msg}")
            return "\n".join(recent)
        except Exception:
            return ""

    async def _respond_to_chat(self, message: str, player: str) -> None:
        """Send player message to LLM and reply in chat right away."""
        try:
            scene = self._current_state.get("scene", "unknown")
            p = self._current_state.get("player", {})
            state_summary = (
                f"Scene: {scene}\n"
                f"Health: {p.get('health', '?')}/{p.get('max_health', '?')}  "
                f"Stamina: {p.get('stamina', '?')}/{p.get('max_stamina', '?')}  "
                f"Mana: {p.get('mana', '?')}/{p.get('max_mana', '?')}\n"
                f"Position: ({p.get('pos_x', '?'):.1f}, {p.get('pos_z', '?'):.1f})  "
                f"In combat: {p.get('in_combat', False)}  Dead: {p.get('is_dead', False)}"
            ) if p else f"Scene: {scene}\n(No player state available)"
            chat_history = self._recent_chat_context()
            prompt = (
                f"Current state:\n{state_summary}\n\n"
                + (f"Recent conversation:\n{chat_history}\n\n" if chat_history else "")
                + f"{player} says: {message}"
            )
            reply = await self._llm.complete(CHAT_SYSTEM_PROMPT, prompt, task="chat")
            if reply:
                # Strip any quotes the LLM might wrap around its response
                reply = reply.strip().strip('"').strip("'")
                await self._game.say(reply)
                self._log_chat("voyager", reply)
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
        await self._navigate_to(target["x"], target["y"], target["z"], run=run)

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
        self._log_chat("voyager", "Looking around...")
        self._scan_for_player = True
        await self._game.scan_nearby(radius=30.0)
        return True

    async def _try_report_goals(self, message: str) -> bool:
        """Report active goals when asked."""
        if not _GOALS_RE.search(message):
            return False
        goal = self._goals.top_priority()
        active = self._goals.active_session_goals()
        if not active:
            await self._game.say("I don't have any specific goals right now. Just exploring.")
        else:
            top = f"Right now: {goal.description}." if goal else ""
            others = len(active) - 1
            suffix = f" (and {others} more)" if others > 0 else ""
            await self._game.say(f"{top}{suffix}")
        logger.info(f"[Goals] Reported {len(active)} active goals")
        return True

    async def _try_report_status(self, message: str) -> bool:
        """Report current nav/health status when asked."""
        if not _STATUS_RE.search(message):
            return False
        player = self._current_state.get("player", {})
        hp = player.get("health", "?")
        max_hp = player.get("max_health", "?")
        scene = self._current_state.get("scene", "unknown")
        skill = self._current_skill_queue[0].name if self._current_skill_queue else "nothing"
        await self._game.say(
            f"HP {hp:.0f}/{max_hp:.0f} in {scene}. Currently doing: {skill}."
        )
        return True

    async def _try_stop(self, message: str) -> bool:
        """Cancel active navigation when player says stop."""
        if not _STOP_RE.search(message):
            return False
        await self._game.navigate_cancel()
        self._current_skill_queue.clear()
        reply = "Stopped."
        await self._game.say(reply)
        self._log_chat("voyager", reply)
        logger.info("[Chat] Player commanded stop — cleared skill queue and cancelled nav")
        return True

    async def _try_move(self, message: str) -> bool:
        """Handle directional movement commands: 'go forward', 'move north', etc.

        In dev mode (autonomous_movement=false): executes immediately.
        In autonomous mode (autonomous_movement=true): passes to the agent's
        decision layer — the agent may comply, refuse, or do something else.
        """
        if not _MOVE_RE.search(message):
            return False

        if self._autonomous_movement:
            # TODO: route through agent decision layer — weigh suggestion against
            # current health, goals, preferences, and danger level.
            # For now, fall through to LLM chat response which will at least
            # reason about whether to comply.
            return False

        player = self._current_state.get("player", {})
        px = float(player.get("pos_x", 0))
        py = float(player.get("pos_y", 0))
        pz = float(player.get("pos_z", 0))

        # If all three are zero the state hasn't populated yet — don't navigate to (0,0,0)
        if not player or (px == 0.0 and py == 0.0 and pz == 0.0):
            await self._game.say("I don't have position data yet.")
            return True

        m = _DIRECTION_RE.search(message)
        direction = m.group(1).lower() if m else "forward"
        run = bool(_RUN_RE.search(message))

        # Cardinal directions use fixed world axes
        # Relative directions (forward/back/left/right) use character facing
        cardinal_offsets = {
            "north": (0, _MOVE_STEP),
            "south": (0, -_MOVE_STEP),
            "east":  (_MOVE_STEP, 0),
            "west":  (-_MOVE_STEP, 0),
        }
        if direction in cardinal_offsets:
            dx, dz = cardinal_offsets[direction]
        else:
            # Relative to camera facing — camera_rotation_y is euler Y in degrees
            rot_y = float(player.get("camera_rotation_y", player.get("rotation_y", 0)))
            rad = math.radians(rot_y)
            # Unity: Y rotation 0 = +Z (north), 90 = +X (east)
            fwd_x, fwd_z = math.sin(rad), math.cos(rad)
            right_x, right_z = math.cos(rad), -math.sin(rad)
            relative_offsets = {
                "forward":  (fwd_x * _MOVE_STEP, fwd_z * _MOVE_STEP),
                "backward": (-fwd_x * _MOVE_STEP, -fwd_z * _MOVE_STEP),
                "back":     (-fwd_x * _MOVE_STEP, -fwd_z * _MOVE_STEP),
                "left":     (-right_x * _MOVE_STEP, -right_z * _MOVE_STEP),
                "right":    (right_x * _MOVE_STEP, right_z * _MOVE_STEP),
            }
            dx, dz = relative_offsets.get(direction, (fwd_x * _MOVE_STEP, fwd_z * _MOVE_STEP))
        tx, tz = px + dx, pz + dz

        await self._navigate_to(tx, py, tz, run=run)
        verb = "Running" if run else "Moving"
        reply = f"{verb} {direction}."
        await self._game.say(reply)
        self._log_chat("voyager", reply)
        logger.info(f"[Nav] Player-commanded move {direction}: ({px:.1f},{pz:.1f}) → ({tx:.1f},{tz:.1f})")
        return True

    async def _try_wander(self, message: str) -> bool:
        """Handle directionless walk requests: 'walk around', 'wander', 'explore'. Picks a random direction."""
        if not _WANDER_RE.search(message):
            return False
        # Don't intercept if a specific direction was already given (_try_move handles those)
        if _DIRECTION_RE.search(message):
            return False

        player = self._current_state.get("player", {})
        px = float(player.get("pos_x", 0))
        py = float(player.get("pos_y", 0))
        pz = float(player.get("pos_z", 0))

        if not player or (px == 0.0 and py == 0.0 and pz == 0.0):
            await self._game.say("I don't have position data yet.")
            return True

        direction = random.choice(["north", "south", "east", "west"])
        offsets = {"north": (0, _MOVE_STEP), "south": (0, -_MOVE_STEP),
                   "east": (_MOVE_STEP, 0), "west": (-_MOVE_STEP, 0)}
        dx, dz = offsets[direction]
        tx, tz = px + dx, pz + dz

        run = bool(_RUN_RE.search(message))
        await self._navigate_to(tx, py, tz, run=run)
        verb = "Running" if run else "Moving"
        reply = f"{verb} {direction}."
        await self._game.say(reply)
        self._log_chat("voyager", reply)
        logger.info(f"[Nav] Wander {direction}: ({px:.1f},{pz:.1f}) → ({tx:.1f},{tz:.1f})")
        return True

    # Only hard-filter pure engine internals — never physical world objects
    _ENGINE_NOISE = (
        "camera", "directionallight", "ambientlight", "audiosource", "audiomixer",
        "skydome", "skybox", "billboard", "shadowcaster", "reflectionprobe",
        "defeatspawn", "spawnpoint", "spawnpos", "position1", "position2",
        "lod_", "_lod", "pfx_", "vfx_", "_pfx", "_vfx", "particlesystem",
        "terrain_chunk", "occluder", "navmesh", "windzone",
        "boxcollider", "colliderholder", "meshcollider", "capsulecollider",
        "cube", "plane", "sphere", "cylinder", "quad",
    )

    @staticmethod
    def _base_name(name: str) -> str:
        """Strip instance suffixes like ' (1)', ' (2)' to deduplicate."""
        return re.sub(r"\s*\(\d+\)$", "", name)

    def _filter_objects(self, objects: list[dict]) -> list[dict]:
        """Keep physical world objects; strip engine internals and deduplicate."""
        result = []
        seen_bases: set[str] = set()
        for obj in objects:
            if not obj.get("active"):
                continue
            name_lower = obj.get("name", "").lower().replace(" ", "")
            # Always keep characters
            if obj.get("has_character"):
                result.append(obj)
                continue
            # Drop pure engine internals
            if any(n in name_lower for n in self._ENGINE_NOISE):
                continue
            # Deduplicate — keep first instance of each base name (closest)
            base = self._base_name(obj.get("name", "")).lower()
            if base in seen_bases:
                continue
            seen_bases.add(base)
            # Keep anything with a physical presence (collider = exists in world)
            if obj.get("has_collider"):
                result.append(obj)
        result.sort(key=lambda o: (0 if o.get("has_character") else 1, o.get("distance", 999)))
        return result

    async def _on_scan_result(self, msg: dict) -> None:
        """Handle scan results from the mod — always log, only speak if player asked."""
        objects: list[dict] = msg.get("objects", [])
        count = msg.get("count", 0)
        logger.info(f"[Scan] {count} objects found:")
        for obj in objects:
            logger.info(
                f"  {obj.get('name'):<40} dist={obj.get('distance'):>5} "
                f"tag={obj.get('tag')} char={obj.get('has_character')} dead={obj.get('is_dead')}"
            )

        meaningful = self._filter_objects(objects)
        self._nearby_objects = meaningful  # cache for autonomous decisions

        if not self._scan_for_player:
            return  # background scan — don't clutter chat
        self._scan_for_player = False

        if not meaningful:
            await self._game.say("I don't see anything of note nearby.")
            return

        # Build a compact scene summary for the LLM
        scene = self._current_state.get("scene", "unknown")
        characters, props = [], []
        for o in meaningful[:30]:
            dist = o.get("distance", "?")
            name = o.get("name", "?")
            if o.get("has_character"):
                state = "dead" if o.get("is_dead") else "alive"
                characters.append(f"- {name} ({dist}m, {state})")
            else:
                props.append(f"- {name} ({dist}m)")

        sections = []
        if characters:
            sections.append("Characters/creatures:\n" + "\n".join(characters))
        if props:
            sections.append("Objects/props:\n" + "\n".join(props))
        object_list = "\n\n".join(sections)

        prompt = (
            f"Location: {scene}\n\n"
            f"Nearby objects (internal game names — translate to plain English):\n{object_list}\n\n"
            "List what you can see in one or two plain sentences. "
            "Translate names directly: Candle_01 = candle, WoodenChest = wooden chest, "
            "IronSword = iron sword, Wolf = wolf, NPC_Merchant = merchant, etc. "
            "Just list the things. No atmosphere, no descriptions of feeling."
        )
        reply = await self._llm.complete(
            "You are an AI agent. Translate a list of game object names into a plain factual sentence about what is nearby. Be direct.",
            prompt,
            task="scan",
        )
        if reply:
            reply = reply.strip().strip('"').strip("'")
            await self._game.say(reply)
            self._log_chat("voyager", reply)

    async def _navigate_to(self, x: float, y: float, z: float, run: bool = False) -> None:
        """Wrapper around game_client.navigate_to that tracks navigation state."""
        player = self._current_state.get("player", {})
        px = float(player.get("pos_x", 0))
        pz = float(player.get("pos_z", 0))
        self._is_navigating = True
        self._nav_pos_snapshot = (px, pz)
        self._nav_snapshot_time = time.time()
        await self._game.navigate_to(x, y, z, run=run)

    def _try_screen_message(self, message: str) -> None:
        """React when a new on-screen message appears."""
        logger.info(f"[ScreenMsg] {message}")
        # If it looks like a door/entrance prompt, add to strategy context
        lower = message.lower()
        if any(k in lower for k in ("enter", "door", "portal", "transition", "leave", "exit")):
            self._pending_chat.append({"message": f"[Screen] {message}", "player": "system"})

    async def _on_nav_arrived(self, msg: dict) -> None:
        logger.info("[Nav] Arrived at destination.")
        self._is_navigating = False
        asyncio.create_task(self._run_strategy())  # plan next move

    async def _on_nav_failed(self, msg: dict) -> None:
        reason = msg.get("reason", "unknown")
        logger.warning(f"[Nav] Navigation failed: {reason}")
        self._is_navigating = False
        asyncio.create_task(self._run_strategy())  # re-plan with different direction

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
        cycle = 0
        while True:
            await asyncio.sleep(self._strategy_interval)
            try:
                await self._run_strategy()
                cycle += 1
                if cycle % 3 == 0:  # Background scan every ~90s
                    await self._game.scan_nearby(radius=40.0)
                if cycle % 10 == 0:  # Save LLM usage every ~5 minutes
                    self._llm.save_usage()
            except Exception as e:
                logger.error(f"Strategy loop error: {e}")

    async def _run_strategy(self) -> None:
        if self._strategy_lock.locked():
            return  # a strategy cycle is already running — skip
        async with self._strategy_lock:
            await self._run_strategy_inner()

    async def _run_strategy_inner(self) -> None:
        goal = self._goals.top_priority()
        recent = self._journal.recent(5)
        familiar = self._map.most_familiar(3)

        personality = self._reward.preferences.describe_personality()
        combat_exp = self._combat.describe_combat_experience()

        p = self._current_state.get("player", {})
        scene = self._current_state.get("scene", "unknown")
        hp = p.get("health", 0)
        max_hp = p.get("max_health", 100)
        stam = p.get("stamina", 0)
        max_stam = p.get("max_stamina", 100)
        mana = p.get("mana", 0)
        max_mana = p.get("max_mana", 0)
        state_summary = (
            f"Scene: {scene}\n"
            f"Health: {hp:.0f}/{max_hp:.0f} ({hp/max(1,max_hp)*100:.0f}%)\n"
            f"Stamina: {stam:.0f}/{max_stam:.0f} ({stam/max(1,max_stam)*100:.0f}%)\n"
            f"Mana: {mana:.0f}/{max_mana:.0f}\n"
            f"Position: ({p.get('pos_x', 0):.0f}, {p.get('pos_z', 0):.0f})\n"
            f"In combat: {p.get('in_combat', False)}  Dead: {p.get('is_dead', False)}"
        )
        # Compact nearby objects summary
        nearby_summary = ""
        if self._nearby_objects:
            items = []
            for o in self._nearby_objects[:8]:
                label = o.get("name", "?")
                dist = o.get("distance", "?")
                if o.get("has_character"):
                    state = "dead" if o.get("is_dead") else "alive"
                    items.append(f"{label} ({dist}m, {state})")
                else:
                    items.append(f"{label} ({dist}m)")
            nearby_summary = f"\nNearby objects: {', '.join(items)}"

        # Nearby interactions summary
        interactions_summary = ""
        if self._nearby_interactions:
            parts = [
                f"{i.get('label', i.get('uid', '?'))} ({i.get('distance', '?')}m)"
                for i in self._nearby_interactions[:5]
            ]
            interactions_summary = f"\nNearby interactions: {', '.join(parts)}"

        # Inventory summary
        inventory_summary = ""
        pouch = self._inventory.get("pouch", [])
        equipped = self._inventory.get("equipped", {})
        if pouch:
            food_items = [i["name"] for i in pouch if i.get("is_food")]
            other_items = [i["name"] for i in pouch if not i.get("is_food")]
            inv_parts = []
            if food_items:
                inv_parts.append(f"food: {', '.join(food_items[:5])}")
            if other_items:
                inv_parts.append(f"items: {', '.join(other_items[:5])}")
            inventory_summary = f"\nInventory — {'; '.join(inv_parts)}"
        if equipped:
            eq_parts = [f"{k}: {v}" for k, v in equipped.items() if v]
            if eq_parts:
                inventory_summary += f"\nEquipped: {', '.join(eq_parts)}"

        screen_msg_summary = f"\nScreen message: {self._screen_message}" if self._screen_message else ""

        user_msg = f"""Current state:
{state_summary}{nearby_summary}{interactions_summary}{inventory_summary}{screen_msg_summary}

Active goal: {goal.description if goal else 'none'}
Recent journal: {recent}
Familiar locations: {[l.scene for l in familiar]}
Personality: {personality}
Combat experience: {combat_exp}
Pending player messages: {self._pending_chat}"""

        response_text = await self._llm.complete(STRATEGY_SYSTEM_PROMPT, user_msg, task="strategy")
        if not response_text:
            return

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

        # If no skills queued, handle common intents directly
        if not self._current_skill_queue:
            await self._execute_intent(intent, decision)

        # If still no skills, try to generate one via the sandbox
        if not self._current_skill_queue:
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

    # ── Intent execution (when no skills match) ─────────────────────────

    async def _execute_intent(self, intent: str, decision: dict) -> None:
        """Directly execute common intents that don't need skill DB entries."""
        player = self._current_state.get("player", {})
        px = float(player.get("pos_x", 0))
        py = float(player.get("pos_y", 0))
        pz = float(player.get("pos_z", 0))

        if px == 0.0 and py == 0.0 and pz == 0.0:
            return  # no valid position yet

        if intent == "explore":
            await self._auto_explore(px, py, pz, decision.get("direction"))
        elif intent == "eat":
            item_name = decision.get("item")
            pouch = self._inventory.get("pouch", [])
            if item_name:
                food = next((i for i in pouch if item_name.lower() in i["name"].lower()), None)
            else:
                food = next((i for i in pouch if i.get("is_food")), None)
            if food:
                await self._game.use_item(food["name"])
                logger.info(f"[Eat] Eating {food['name']}")
            else:
                logger.info("[Eat] No food in pouch")
        elif intent == "use_item":
            item_name = decision.get("item")
            pouch = self._inventory.get("pouch", [])
            if item_name:
                target = next((i for i in pouch if item_name.lower() in i["name"].lower()), None)
                if target:
                    if target.get("is_equipment"):
                        await self._game.equip_item(target["name"])
                        logger.info(f"[Equip] Equipping {target['name']}")
                    else:
                        await self._game.use_item(target["name"])
                        logger.info(f"[Use] Using {target['name']}")
                else:
                    logger.info(f"[Use] Item '{item_name}' not found in pouch")
            else:
                logger.info("[Use] No item specified in decision")
        elif intent == "interact":
            uid = decision.get("interaction_uid", "")
            if not uid and self._nearby_interactions:
                nearest = self._nearby_interactions[0]
                uid = nearest.get("uid", "")
                label = nearest.get("label", uid)
                logger.info(f"[Interact] Triggering {label} (uid={uid})")
            if uid:
                await self._game.trigger_interaction(uid)
            else:
                await self._game.interact(radius=3.0)
                logger.info("[Interact] No uid — falling back to proximity interact")
        elif intent == "investigate":
            # If we have cached scan results with characters/items, walk to the closest
            interesting = [o for o in self._nearby_objects
                           if o.get("has_character") or o.get("has_item")]
            if interesting:
                target = interesting[0]
                tx = float(target.get("x", px))
                tz = float(target.get("z", pz))
                await self._navigate_to(tx, py, tz, run=False)
                logger.info(f"[Nav] Investigating {target.get('name')} at ({tx:.1f}, {tz:.1f})")
            else:
                # No cached results — scan first
                await self._game.scan_nearby(radius=40.0)
        elif intent == "flee":
            # Run in a random direction away from danger
            angle = random.uniform(0, 2 * math.pi)
            tx = px + math.sin(angle) * 30.0
            tz = pz + math.cos(angle) * 30.0
            await self._navigate_to(tx, py, tz, run=True)
            logger.info(f"[Nav] Fleeing to ({tx:.1f}, {tz:.1f})")

    _CARDINAL_ANGLES = {
        "north": 0.0, "northeast": math.pi / 4, "east": math.pi / 2,
        "southeast": 3 * math.pi / 4, "south": math.pi,
        "southwest": 5 * math.pi / 4, "west": 3 * math.pi / 2,
        "northwest": 7 * math.pi / 4,
    }

    _ITEM_NAME_RE = re.compile(r"^\d{7}_", re.ASCII)  # e.g. 5300120_, 4100030_

    async def _auto_explore(self, px: float, py: float, pz: float,
                            direction: str | None = None) -> None:
        """Pick an exploration target and walk there.

        If there are uncollected item-like objects nearby (names starting with
        digit prefixes like 5300120_), navigate to the closest one instead of
        picking a random direction.
        """
        # Check for nearby items to collect before exploring randomly
        item_objects = [
            o for o in self._nearby_objects
            if self._ITEM_NAME_RE.match(o.get("name", "")) and not o.get("has_character")
        ]
        if item_objects:
            target = item_objects[0]  # already sorted by distance
            tx = float(target.get("x", px))
            tz = float(target.get("z", pz))
            await self._navigate_to(tx, py, tz, run=False)
            logger.info(f"[Nav] Moving toward nearby item {target.get('name')} at ({tx:.1f}, {tz:.1f})")
            return

        dist = random.uniform(15.0, 40.0)

        if direction and direction.lower() in self._CARDINAL_ANGLES:
            # LLM suggested a direction — use it with slight randomness
            base_angle = self._CARDINAL_ANGLES[direction.lower()]
            angle = base_angle + random.uniform(-0.3, 0.3)
        else:
            angle = random.uniform(0, 2 * math.pi)

        tx = px + math.sin(angle) * dist
        tz = pz + math.cos(angle) * dist
        await self._navigate_to(tx, py, tz, run=False)
        dir_label = direction or "random"
        logger.info(f"[Nav] Auto-exploring {dir_label} toward ({tx:.1f}, {tz:.1f})")

    # ── Rule engine ──────────────────────────────────────────────────────────

    async def _rule_loop(self) -> None:
        # State is pushed by the mod every 2s — no need to poll.
        # Just run skill execution on the same cadence.
        while True:
            await asyncio.sleep(self._rule_interval)
            try:
                await self._poll_dashboard_chat()
                # In combat: pause non-combat skills and let the agent react
                if self._current_state.get("player", {}).get("in_combat", False):
                    await self._handle_in_combat()
                else:
                    await self._execute_next_skill()
            except Exception as e:
                logger.error(f"Rule engine error: {e}")

    async def _handle_in_combat(self) -> None:
        """
        Very basic combat reaction: the agent doesn't fight yet (no combat
        commands implemented), but it pauses its current plan, cancels any
        active navigation, and cries for help if health is critical.
        """
        player = self._current_state.get("player", {})
        hp = player.get("health", 100)
        max_hp = player.get("max_health", 100)
        hp_pct = hp / max(1, max_hp)

        # Cancel navigation — don't walk into danger
        await self._game.navigate_cancel()

        if hp_pct < 0.2:
            logger.warning(f"[Combat] HP critical: {hp_pct:.0%}")
            await self._game.say("Help! I'm in danger!")

    async def _execute_next_skill(self) -> None:
        if not self._current_skill_queue:
            return

        skill = self._current_skill_queue[0]

        # Check preconditions
        if not self._check_preconditions(skill):
            logger.debug(f"Preconditions not met for {skill.name}, skipping.")
            return

        # Execute — handle "wait" locally so it never reaches the game mod
        logger.info(f"Executing skill: {skill.name}")
        if skill.action_type == "wait":
            seconds = float(skill.parameters.get("seconds", 2.0))
            await asyncio.sleep(seconds)
        else:
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

    _SKILL_PROPOSAL_COOLDOWN = 300.0  # 5 minutes between proposals for the same intent

    async def _maybe_propose_new_skill(self, intent: str, reasoning: str) -> None:
        """
        Occasionally ask the LLM to write a new sandbox skill if the current
        intent has no matching skills in the database.
        Only runs when the skill queue is empty and intent is known.
        Has a per-intent cooldown to avoid spamming the LLM.
        """
        if self._current_skill_queue:
            return  # already have skills to run
        if not intent or intent == "explore":
            return  # too generic to write a useful skill for

        import time
        now = time.time()
        last = self._last_skill_proposal.get(intent, 0.0)
        if now - last < self._SKILL_PROPOSAL_COOLDOWN:
            return  # still cooling down
        self._last_skill_proposal[intent] = now

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
        code = await self._llm.complete("You write safe Python code.", PROPOSE_PROMPT, task="code")
        if not code:
            return

        # Strip markdown fences if LLM added them
        code = code.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        skill_name = f"auto_{intent}"
        await self.propose_skill(skill_name, code, description=f"Auto-generated for intent={intent}")
