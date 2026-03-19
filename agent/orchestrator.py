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
from keybinding_learner import KeybindingLearner
from social.memory import SocialMemoryManager
from social.relationships import RelationshipEngine

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
- Move and navigate through the world
- Stop moving
- Scan nearby objects, creatures, and items
- Interact with objects, doors, and NPCs
- Pick up items from the ground
- Use consumable items (eat food, drink water, use potions)
- Equip and unequip gear
- Speak in chat
- Read your known skills and abilities
- Open game menus: inventory, skills, equipment, map, quest journal

What you cannot do yet: attack, cast spells in combat, crouch.
If asked to do something outside these capabilities, be honest about it.

Note: "MainMenu_Empty" is the internal name for the lighthouse/starting area. The game IS loaded.

Speak in first person. Keep replies short — 1-2 sentences. Do not invent values
for health, position, or inventory — use only what the game state actually shows.
Respond with ONLY the message text. No JSON, no formatting, no quotes."""

STRATEGY_SYSTEM_PROMPT = """You are Voyager, an AI that exists inside the world of Outward.
You inhabit a character named AgentNeo. Josh created you. You make your own decisions.
You are accumulating experiences and forming your own understanding of this world.

Given the current game state and your goals, decide what to do next.

IMPORTANT — scene names: Outward uses internal Unity scene names that don't describe content.
"MainMenu_Empty" is the lighthouse/starting area — the game IS fully loaded and playable.
Never assume the game isn't loaded based on scene name alone. If health > 0, you are in-game.

Understanding the stats:
- Health, Stamina, Mana, Food, Drink, Sleep shown as current/max (percentage).
- Only act on a stat if it is below 50% — do NOT treat 90% as low.
- in_combat: true means actively fighting — survival first.
- is_dead: true means you died — reflect briefly, then move on.

Survival priorities (only when below 50%):
- Food low → intent: eat (if food in inventory) or gather_food
- Drink low → intent: drink (consume water/drink item from inventory)
- Sleep low → intent: sleep (find a bed or campfire)
- Health low + food available → intent: eat
- Health low + no food → intent: rest or flee

Available intents: explore, gather_food, eat, drink, sleep, use_item, rest, interact,
                   investigate, open_menu, flee, trade, craft

Use open_menu when: player asks to open a menu, or you want to read your skills/equipment.
  menu values: "inventory", "skills", "map", "equipment", "quest"
Use interact when there is something in nearby_interactions worth triggering.
Use investigate when there are nearby objects or characters worth approaching.
When stats are healthy (all above 50%): freely explore and interact with the world.

Respond with ONLY a JSON object (no markdown, no extra text):
{
  "intent": "<action tag>",
  "reasoning": "<one sentence>",
  "direction": "<optional: north/south/east/west or null>",
  "item": "<item name from inventory if intent is eat/drink/use_item/equip, else null>",
  "interaction_uid": "<uid from nearby_interactions if intent is interact, else null>",
  "menu": "<menu name if intent is open_menu, else null>",
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
        self._keybindings = KeybindingLearner("./data")
        self._social = SocialMemoryManager("./data/social_memory.jsonl")
        self._relationships = RelationshipEngine("./data/relationships.json")

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
        self._known_skills: list[dict] = []  # Known skills from SkillKnowledge
        self._pending_interact_uid: str = ""  # Interact with this uid after nav arrives
        self._blocked_nav_targets: set[tuple[int, int]] = set()  # (x//5, z//5) grid cells
        self._current_nav_target: tuple[float, float] = (0.0, 0.0)  # (x, z) of current nav

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
        self._game.on("skills", self._on_skills)

    async def run(self) -> None:
        """Start all loops concurrently."""
        await asyncio.gather(
            self._game.connect(),
            self._strategy_loop(),
            self._rule_loop(),
        )

    # ── Event handlers ──────────────────────────────────────────────────────

    async def _on_connected(self, _msg: dict) -> None:
        """Sync autonomous mode flag and read initial skill list."""
        await self._game.set_autonomous(self._autonomous_movement)
        logger.info(f"Synced autonomous_movement={self._autonomous_movement} to mod")
        await self._game.read_skills()
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
            py = float(player.get("pos_y", 0))
            now = time.time()
            if now - self._nav_snapshot_time >= self._nav_check_interval:
                sx, sz = self._nav_pos_snapshot
                dist_moved = math.sqrt((px - sx) ** 2 + (pz - sz) ** 2)
                if dist_moved < 0.5:
                    self._nav_stuck_count = getattr(self, "_nav_stuck_count", 0) + 1
                    logger.warning(f"[Nav] Position unchanged (stuck #{self._nav_stuck_count}) — trying escape")
                    self._is_navigating = False
                    if self._nav_stuck_count <= 3:
                        # Try a random escape direction before re-planning
                        angle = random.uniform(0, 2 * math.pi)
                        ex = px + math.sin(angle) * 8.0
                        ez = pz + math.cos(angle) * 8.0
                        await self._navigate_to(ex, py, ez, run=False)
                        await self._game.say(f"I seem to be stuck. Trying a different angle.")
                    else:
                        self._nav_stuck_count = 0
                        await self._game.say("I can't seem to move from here. Re-evaluating.")
                        asyncio.create_task(self._run_strategy())
                else:
                    self._nav_stuck_count = 0
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

        # Record interaction in social memory
        scene = self._current_state.get("scene", "unknown")
        sentiment = self._relationships.infer_sentiment(message)
        trait = self._relationships.infer_trait(message)
        ix = self._social.record_message(player, message, scene, sentiment=sentiment,
                                         tags=[trait] if trait else [])
        self._relationships.update_from_interaction(player, sentiment, scene, trait)

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
            # Include relationship context for this player
            rel = self._relationships.get(player)
            social_hint = (
                f"\nYou know {player}: {rel.short_summary()}"
                if self._relationships.has_met(player) else ""
            )
            prompt_with_social = prompt + social_hint

            reply = await self._llm.complete(CHAT_SYSTEM_PROMPT, prompt_with_social, task="chat")
            if reply:
                # Strip any quotes the LLM might wrap around its response
                reply = reply.strip().strip('"').strip("'")
                await self._game.say(reply)
                self._log_chat("voyager", reply)
                logger.info(f"[Chat] Voyager replied: {reply}")
                # Record the agent's response in social memory
                recent_ix = self._social.for_player(player, n=1)
                if recent_ix:
                    self._social.update_response(recent_ix[0], reply)
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

    async def _on_skills(self, msg: dict) -> None:
        skills = msg.get("skills", [])
        self._known_skills = skills
        names = [s.get("name", "?") for s in skills]
        logger.info(f"[Skills] Received {len(skills)} known skills: {', '.join(names[:10])}")

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

    def _is_nav_blocked(self, x: float, z: float) -> bool:
        """Check if a target coordinate is near a known blocked cell (5m grid)."""
        cell = (int(x) // 5, int(z) // 5)
        # Also check adjacent cells — blocked walls affect a wider area
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if (cell[0] + dx, cell[1] + dz) in self._blocked_nav_targets:
                    return True
        return False

    def _mark_nav_blocked(self, x: float, z: float) -> None:
        """Record that navigation to this area failed."""
        cell = (int(x) // 5, int(z) // 5)
        self._blocked_nav_targets.add(cell)
        # Bound memory growth — drop oldest if too many
        if len(self._blocked_nav_targets) > 50:
            self._blocked_nav_targets.pop()

    async def _navigate_to(self, x: float, y: float, z: float, run: bool = False) -> None:
        """Wrapper around game_client.navigate_to that tracks navigation state."""
        # If target is known blocked, jitter it before sending
        if self._is_nav_blocked(x, z):
            jitter_angle = random.uniform(0, 2 * math.pi)
            jitter_dist = random.uniform(8.0, 15.0)
            x += math.sin(jitter_angle) * jitter_dist
            z += math.cos(jitter_angle) * jitter_dist
            logger.info(f"[Nav] Blocked target jittered to ({x:.1f},{z:.1f})")

        self._current_nav_target = (x, z)
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
        # If we were navigating in order to interact, trigger it now
        if self._pending_interact_uid:
            uid = self._pending_interact_uid
            self._pending_interact_uid = ""
            logger.info(f"[Interact] Arrived — now triggering {uid}")
            await self._game.trigger_interaction(uid)
        asyncio.create_task(self._run_strategy())  # plan next move

    async def _on_nav_failed(self, msg: dict) -> None:
        reason = msg.get("reason", "unknown")
        logger.warning(f"[Nav] Navigation failed: {reason}")
        self._is_navigating = False
        # Remember this coordinate as blocked so we don't try again
        tx, tz = self._current_nav_target
        if tx != 0.0 or tz != 0.0:
            self._mark_nav_blocked(tx, tz)
            scene = self._current_state.get("scene", "unknown")
            self._map.add_note(scene, f"nav blocked near ({tx:.0f},{tz:.0f}): {reason}")
            logger.info(f"[Nav] Marked ({tx:.0f},{tz:.0f}) as blocked in {scene}")
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
                if cycle % 10 == 0:  # Read skills + save LLM usage every ~5 minutes
                    await self._game.read_skills()
                    self._llm.save_usage()
                if cycle % 20 == 0:  # Vision keybinding discovery every ~10 minutes
                    asyncio.create_task(self._keybindings.discover_from_screenshot())
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

        def pct(cur, mx): return f"{cur:.0f}/{mx:.0f} ({cur/max(1,mx)*100:.0f}%)"

        hp,  max_hp   = p.get("health", 0),   p.get("max_health", 100)
        stam,max_stam = p.get("stamina", 0),  p.get("max_stamina", 100)
        mana,max_mana = p.get("mana", 0),     p.get("max_mana", 0)
        food,max_food = p.get("food", 0),       p.get("max_food", 100)
        drink,max_drk = p.get("drink", 0),      p.get("max_drink", 100)
        slp, max_slp  = p.get("sleep", 0),      p.get("max_sleep", 100)
        corr,max_corr = p.get("corruption", 0), p.get("max_corruption", 100)
        temp          = p.get("body_temperature", 20)
        status_effects= p.get("status_effects", [])

        # Only show survival stats if the game is returning valid (non-zero) max values.
        # max=0 means IL2CPP reflection couldn't read the field — treat as unknown, not critical.
        def stat_line(label: str, cur: float, mx: float) -> str:
            if mx <= 0:
                return ""  # unreadable — omit rather than mislead the LLM
            return f"{label}: {pct(cur, mx)}\n"

        state_summary = (
            f"Scene: {scene}\n"
            f"Health:   {pct(hp,  max_hp)}\n"
            f"Stamina:  {pct(stam,max_stam)}\n"
            + stat_line("Mana",  mana, max_mana)
            + stat_line("Food",  food, max_food)
            + stat_line("Drink", drink, max_drk)
            + stat_line("Sleep",      slp,  max_slp)
            + stat_line("Corruption", corr, max_corr)
            + (f"Temp:     {temp:.1f}°\n" if temp != 20.0 else "")
            + (f"Status effects: {', '.join(status_effects)}\n" if status_effects else "")
            + f"Position: ({p.get('pos_x', 0):.0f}, {p.get('pos_z', 0):.0f})\n"
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

        # Known skills summary
        skills_summary = ""
        if self._known_skills:
            skill_names = [s.get("name", "?") for s in self._known_skills[:15]]
            skills_summary = f"\nKnown skills/abilities: {', '.join(skill_names)}"

        # Social context
        social_context = self._social.context_block(scene, n=4)
        known_players = self._relationships.context_block()

        # Keybinding context — what keys the agent can press
        keybinding_context = self._keybindings.as_context_string()

        # Blocked nav targets — the LLM should avoid these grid cells
        blocked_summary = ""
        if self._blocked_nav_targets:
            samples = list(self._blocked_nav_targets)[:5]
            blocked_summary = "\nBlocked nav areas (5m grid): " + ", ".join(
                f"({x*5},{z*5})" for x, z in samples
            )

        user_msg = f"""Current state:
{state_summary}{nearby_summary}{interactions_summary}{inventory_summary}{skills_summary}{screen_msg_summary}

Active goal: {goal.description if goal else 'none'}
Recent journal: {recent}{blocked_summary}
Familiar locations: {[l.scene for l in familiar]}
Personality: {personality}
Combat experience: {combat_exp}
Known keyboard shortcuts (learned):
{keybinding_context}
Recent player interactions:
{social_context}
Known players:
{known_players}
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

        # Check if any session goals are now complete
        self._check_goal_completion()

        # Add observations to mental map
        self._record_mental_map_notes()

    def _check_goal_completion(self) -> None:
        """Rule-based goal completion — mark goals done based on current game state."""
        p = self._current_state.get("player", {})
        scene = self._current_state.get("scene", "unknown")
        loc = self._map.get(scene)

        for goal in self._goals.active_session_goals():
            gid = goal.id
            completed = False
            reason = ""

            if gid == "explore_starting_area":
                # Complete if we've visited this area at least 3 times
                if loc and loc.visit_count >= 3:
                    completed = True
                    reason = f"visited {loc.scene} {loc.visit_count} times"

            elif gid == "find_food_water":
                food_ok = p.get("max_food", 0) > 0 and (p.get("food", 0) / max(1, p.get("max_food", 1))) > 0.70
                drink_ok = p.get("max_drink", 0) > 0 and (p.get("drink", 0) / max(1, p.get("max_drink", 1))) > 0.70
                if food_ok and drink_ok:
                    completed = True
                    reason = "food and drink above 70%"

            elif gid == "learn_world":
                # Complete after visiting at least 2 distinct scenes
                familiar = self._map.most_familiar(10)
                if len(familiar) >= 2:
                    completed = True
                    reason = f"visited {len(familiar)} locations"

            if completed:
                self._goals.complete(gid)
                logger.info(f"[Goals] Completed '{gid}': {reason}")
                self._journal.record(JournalEntry(
                    text=f"Completed goal '{goal.description}': {reason}",
                    scene=scene,
                    tags=["goal_complete", gid],
                ))
                asyncio.create_task(self._maybe_create_new_goal(goal))

    async def _maybe_create_new_goal(self, completed_goal: Goal) -> None:
        """Ask the LLM to generate a new session goal after completing one."""
        # Only generate new goals occasionally — not after every completion
        active = self._goals.active_session_goals()
        if len(active) >= 3:
            return  # plenty of goals already

        scene = self._current_state.get("scene", "unknown")
        p = self._current_state.get("player", {})
        familiar = self._map.most_familiar(3)
        long_term = self._goals.active_long_term_goals()

        prompt = (
            f"You are Voyager, an autonomous agent playing Outward.\n"
            f"You just completed the goal: '{completed_goal.description}'\n"
            f"Current scene: {scene}\n"
            f"Player health: {p.get('health', '?')}/{p.get('max_health', '?')}\n"
            f"Familiar locations: {[l.scene for l in familiar]}\n"
            f"Long-term ambitions: {[g.description for g in long_term[:3]]}\n"
            f"Active session goals remaining: {[g.description for g in self._goals.active_session_goals()]}\n\n"
            "Propose ONE new short-term session goal (achievable in the next 5-10 minutes) "
            "that builds on your progress. Be specific and actionable.\n"
            "Respond with ONLY JSON: {\"id\": \"snake_case_id\", \"description\": \"...\", \"priority\": <1-10>}"
        )
        try:
            resp = await self._llm.complete("You are a game agent proposing goals.", prompt, task="strategy")
            if not resp:
                return
            resp = resp.strip()
            if resp.startswith("```"):
                resp = "\n".join(resp.split("\n")[1:-1])
            data = json.loads(resp)
            new_goal = Goal(
                id=data.get("id", f"auto_{int(time.time())}"),
                description=data.get("description", "Explore further"),
                priority=int(data.get("priority", 5)),
                tags=["auto_generated"],
            )
            self._goals.add_session_goal(new_goal)
            logger.info(f"[Goals] Created new goal: {new_goal.id} — {new_goal.description}")
            self._journal.record(JournalEntry(
                text=f"Set new goal: {new_goal.description}",
                scene=self._current_state.get("scene", "unknown"),
                tags=["new_goal", "auto_generated"],
            ))
        except Exception as e:
            logger.debug(f"[Goals] Goal generation failed: {e}")

    def _record_mental_map_notes(self) -> None:
        """Add observations to the mental map based on what's currently visible."""
        scene = self._current_state.get("scene", "unknown")
        if not scene or scene == "unknown":
            return

        # Record nearby interactions as scene notes (once per unique name)
        for ix in self._nearby_interactions[:3]:
            label = ix.get("label") or ix.get("uid", "")
            if label and len(label) > 3:
                note = f"interaction: {label}"
                loc = self._map.get(scene)
                if loc is None or note not in loc.notes:
                    self._map.add_note(scene, note)

        # Record enemy encounters as danger notes
        p = self._current_state.get("player", {})
        if p.get("in_combat"):
            note = f"combat encountered here"
            loc = self._map.get(scene)
            if loc is None or note not in loc.notes:
                self._map.add_note(scene, note)

        # Record food/resource item sightings
        food_items = [o for o in self._nearby_objects if o.get("is_food") or
                      any(k in o.get("name", "").lower() for k in ["berry", "mushroom", "seaweed", "fish"])]
        if food_items:
            note = f"food found: {food_items[0].get('name', 'unknown')}"
            loc = self._map.get(scene)
            if loc is None or note not in (loc.notes or []):
                self._map.add_note(scene, note)

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
            direction = decision.get("direction")
            dir_label = direction or "somewhere new"
            await self._game.say(f"Heading {dir_label}.")
            await self._auto_explore(px, py, pz, direction)
        elif intent in ("eat", "drink"):
            item_name = decision.get("item")
            pouch = self._inventory.get("pouch", [])
            _DRINK_KEYWORDS = ("water", "drink", "juice", "tea", "ale", "wine", "brew", "flask", "potion")
            if item_name:
                target = next((i for i in pouch if item_name.lower() in i["name"].lower()), None)
            elif intent == "eat":
                target = next((i for i in pouch if i.get("is_food")), None)
            else:
                target = next(
                    (i for i in pouch if any(k in i["name"].lower() for k in _DRINK_KEYWORDS)
                     and not i.get("is_equipment")),
                    None,
                )
            if target:
                display = target["name"].split("_")[0] if "_" in target["name"] else target["name"]
                verb = "Eating" if intent == "eat" else "Drinking"
                await self._game.say(f"{verb} {display}.")
                await self._game.use_item(target["name"])
                logger.info(f"[{intent.capitalize()}] Using {target['name']}")
            else:
                await self._game.say(f"I'm {'hungry' if intent=='eat' else 'thirsty'} but have nothing to {'eat' if intent=='eat' else 'drink'}. Looking around.")
                logger.info(f"[{intent.capitalize()}] Nothing available in pouch")
                await self._auto_explore(px, py, pz, decision.get("direction"))
        elif intent == "use_item":
            item_name = decision.get("item")
            pouch = self._inventory.get("pouch", [])
            if item_name:
                target = next((i for i in pouch if item_name.lower() in i["name"].lower()), None)
                if target:
                    display = target["name"].split("_")[0] if "_" in target["name"] else target["name"]
                    if target.get("is_equipment"):
                        await self._game.say(f"Equipping {display}.")
                        await self._game.equip_item(target["name"])
                        logger.info(f"[Equip] Equipping {target['name']}")
                    else:
                        await self._game.say(f"Using {display}.")
                        await self._game.use_item(target["name"])
                        logger.info(f"[Use] Using {target['name']}")
                else:
                    await self._game.say(f"I wanted to use {item_name} but couldn't find it.")
                    logger.info(f"[Use] Item '{item_name}' not found in pouch")
            else:
                logger.info("[Use] No item specified in decision")
        elif intent == "sleep":
            # Look for a bed/campfire in nearby interactions to rest at
            sleep_keywords = ("bed", "bedroll", "campfire", "fire", "sleep", "inn", "rest")
            rest_spot = next(
                (i for i in self._nearby_interactions
                 if any(k in i.get("label", "").lower() for k in sleep_keywords)),
                None,
            )
            if rest_spot:
                uid = rest_spot.get("uid", "")
                label = rest_spot.get("label", uid)
                await self._game.say(f"Resting at {label}.")
                logger.info(f"[Sleep] Triggering rest at {label} (uid={uid})")
                await self._game.trigger_interaction(uid)
            elif self._nearby_interactions:
                nearest = self._nearby_interactions[0]
                await self._game.say(f"Trying to rest near {nearest.get('label', 'here')}.")
                logger.info(f"[Sleep] Trying nearest interaction: {nearest.get('label')}")
                await self._game.trigger_interaction(nearest.get("uid", ""))
            else:
                await self._game.say("I need to sleep but there's nowhere nearby. Looking for shelter.")
                logger.info("[Sleep] No rest spot nearby — exploring to find one")
                await self._auto_explore(px, py, pz, decision.get("direction"))
        elif intent == "interact":
            uid = decision.get("interaction_uid", "")
            # Pick target: LLM-specified uid or nearest interaction
            target_interaction = None
            if uid:
                target_interaction = next(
                    (i for i in self._nearby_interactions if i.get("uid") == uid), None
                )
            if not target_interaction and self._nearby_interactions:
                target_interaction = self._nearby_interactions[0]
                uid = target_interaction.get("uid", "")

            if target_interaction:
                dist = target_interaction.get("distance", 0)
                label = target_interaction.get("label", uid)
                # Face camera toward the target
                await self._game.face_point(
                    target_interaction.get("x", px),
                    target_interaction.get("y", py),
                    target_interaction.get("z", pz),
                )
                if dist > 3.0:
                    await self._game.say(f"Moving toward {label}.")
                    tx = target_interaction.get("x", px)
                    ty = target_interaction.get("y", py)
                    tz = target_interaction.get("z", pz)
                    await self._navigate_to(tx, ty, tz, run=False)
                    self._pending_interact_uid = uid
                    logger.info(f"[Interact] Navigating to {label} at {dist:.1f}m first")
                else:
                    await self._game.say(f"Interacting with {label}.")
                    logger.info(f"[Interact] Triggering {label} (uid={uid}, dist={dist:.1f}m)")
                    await self._game.trigger_interaction(uid)
            else:
                await self._game.say("Looking for something to interact with.")
                await self._game.interact(radius=3.0)
                logger.info("[Interact] No uid — falling back to proximity interact")
        elif intent == "investigate":
            interesting = [o for o in self._nearby_objects
                           if o.get("has_character") or o.get("has_item")]
            if interesting:
                target = interesting[0]
                tx = float(target.get("x", px))
                tz = float(target.get("z", pz))
                name = target.get("name", "something")
                await self._game.say(f"Going to investigate {name}.")
                await self._game.face_point(tx, py, tz)
                await self._navigate_to(tx, py, tz, run=False)
                logger.info(f"[Nav] Investigating {name} at ({tx:.1f}, {tz:.1f})")
            else:
                await self._game.say("Scanning the area.")
                await self._game.scan_nearby(radius=40.0)
        elif intent == "open_menu":
            menu = decision.get("menu", "inventory")
            await self._game.say(f"Opening {menu}.")
            await self._game.open_menu(menu)
            logger.info(f"[Menu] Opening {menu}")
        elif intent == "flee":
            angle = random.uniform(0, 2 * math.pi)
            tx = px + math.sin(angle) * 30.0
            tz = pz + math.cos(angle) * 30.0
            await self._game.say("Running!")
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
