"""
StateManager — canonical game state with delta computation.

Receives raw game_state messages from GameClient, tracks what changed,
and surfaces meaningful deltas. No game-specific logic — pure observation.
"""
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

STAT_CRITICAL_THRESHOLD = 0.30   # below 30% of max — urgent
STAT_DROP_THRESHOLD     = 0.20   # dropped >20% in one tick — significant


@dataclass
class StateDelta:
    """What changed between two consecutive game_state messages."""
    scene_changed: bool = False
    prev_scene: str = ""
    new_scene: str = ""

    combat_entered: bool = False
    combat_exited: bool = False
    death_occurred: bool = False

    # Stat changes as fraction of max (negative = dropped)
    health_delta: float = 0.0
    food_delta: float   = 0.0
    drink_delta: float  = 0.0

    # Stat critical flags (fell below threshold this tick)
    stat_critical: list[str] = field(default_factory=list)

    nearby_interactions_changed: bool = False
    screen_message_changed: bool = False
    new_screen_message: str = ""


class StateManager:
    """Tracks canonical game state and emits deltas. Zero game knowledge."""

    def __init__(self) -> None:
        self._current: dict[str, Any] = {}
        self._prev: dict[str, Any] = {}

        # Derived caches updated each game_state push
        self._nearby_interactions: list[dict] = []
        self._nearby_objects: list[dict] = []      # from scan_result
        self._inventory: dict = {}
        self._screen_message: str = ""
        self._known_skills: list[dict] = []

        # Navigation state
        self._is_navigating: bool = False
        self._nav_target: tuple[float, float] = (0.0, 0.0)
        self._nav_snapshot: tuple[float, float] = (0.0, 0.0)
        self._nav_snapshot_time: float = 0.0
        self._nav_check_interval: float = 5.0
        self._nav_stuck_count: int = 0
        self._blocked: set[tuple[int, int]] = set()
        self._blocked_order: deque[tuple[int, int]] = deque(maxlen=50)  # evicts oldest first

        # Debounce for screen-triggered key presses
        self._last_key_press_time: float = 0.0

        # Session start time
        self._session_start: float = time.time()

        # Registered delta handlers
        self._delta_handlers: list[Callable[[StateDelta], None]] = []

    # ── Registration ─────────────────────────────────────────────────────────

    def on_delta(self, handler: Callable[[StateDelta], None]) -> None:
        self._delta_handlers.append(handler)

    # ── Ingest ───────────────────────────────────────────────────────────────

    def update(self, msg: dict) -> StateDelta:
        """Process a new game_state message. Returns the computed delta."""
        self._prev = self._current
        self._current = msg

        prev_interactions = self._nearby_interactions
        self._nearby_interactions = msg.get("nearby_interactions", [])
        self._inventory = msg.get("inventory", {})
        new_screen = msg.get("screen_message", "")

        delta = self._compute_delta(new_screen, prev_interactions)
        self._screen_message = new_screen

        for handler in self._delta_handlers:
            try:
                handler(delta)
            except Exception as e:
                logger.warning(f"[State] Delta handler error: {e}")

        return delta

    def update_nearby_objects(self, objects: list[dict]) -> None:
        self._nearby_objects = objects

    def update_known_skills(self, skills: list[dict]) -> None:
        self._known_skills = skills

    # ── Navigation tracking ──────────────────────────────────────────────────

    def set_navigating(self, x: float, z: float) -> None:
        p = self.player
        self._is_navigating = True
        self._nav_target = (x, z)
        self._nav_snapshot = (float(p.get("pos_x", 0)), float(p.get("pos_z", 0)))
        self._nav_snapshot_time = time.time()
        self._nav_stuck_count = 0

    def set_arrived(self) -> None:
        self._is_navigating = False
        self._nav_stuck_count = 0

    def set_nav_failed(self, x: float | None = None, z: float | None = None) -> None:
        self._is_navigating = False
        tx, tz = (x, z) if x is not None else self._nav_target
        self._mark_blocked(tx, tz)

    def check_stuck(self) -> tuple[bool, float, float, float]:
        """Returns (is_stuck, px, py, pz). Resets navigating flag if stuck."""
        if not self._is_navigating:
            return False, 0.0, 0.0, 0.0
        p = self.player
        px, py, pz = float(p.get("pos_x", 0)), float(p.get("pos_y", 0)), float(p.get("pos_z", 0))
        if time.time() - self._nav_snapshot_time < self._nav_check_interval:
            return False, px, py, pz
        sx, sz = self._nav_snapshot
        if math.sqrt((px - sx) ** 2 + (pz - sz) ** 2) < 0.5:
            self._nav_stuck_count += 1
            logger.warning(f"[Nav] Stuck #{self._nav_stuck_count} at ({px:.0f},{pz:.0f})")
            self._is_navigating = False
            return True, px, py, pz
        self._nav_stuck_count = 0
        self._nav_snapshot = (px, pz)
        self._nav_snapshot_time = time.time()
        return False, px, py, pz

    def is_blocked(self, x: float, z: float) -> bool:
        cell = (int(x) // 5, int(z) // 5)
        return any((cell[0]+dx, cell[1]+dz) in self._blocked for dx in (-1,0,1) for dz in (-1,0,1))

    def mark_blocked(self, x: float, z: float) -> None:
        self._mark_blocked(x, z)

    def record_key_press(self) -> None:
        self._last_key_press_time = time.time()

    # ── Property accessors ───────────────────────────────────────────────────

    @property
    def current(self) -> dict[str, Any]:
        return self._current

    @property
    def player(self) -> dict[str, Any]:
        return self._current.get("player", {})

    @property
    def scene(self) -> str:
        return self._current.get("scene", "unknown")

    @property
    def is_navigating(self) -> bool:
        return self._is_navigating

    @property
    def nav_target(self) -> tuple[float, float]:
        return self._nav_target

    @property
    def nav_stuck_count(self) -> int:
        return self._nav_stuck_count

    @property
    def nearby_objects(self) -> list[dict]:
        return self._nearby_objects

    @property
    def nearby_interactions(self) -> list[dict]:
        return self._nearby_interactions

    @property
    def inventory(self) -> dict:
        return self._inventory

    @property
    def screen_message(self) -> str:
        return self._screen_message

    @property
    def known_skills(self) -> list[dict]:
        return self._known_skills

    @property
    def is_in_combat(self) -> bool:
        return bool(self.player.get("in_combat", False))

    @property
    def is_dead(self) -> bool:
        return bool(self.player.get("is_dead", False))

    @property
    def last_key_press_time(self) -> float:
        return self._last_key_press_time

    @property
    def session_elapsed_minutes(self) -> float:
        return (time.time() - self._session_start) / 60

    # ── Internals ────────────────────────────────────────────────────────────

    def _compute_delta(self, new_screen: str, prev_interactions: list[dict]) -> StateDelta:
        delta = StateDelta()

        # Scene change
        prev_scene = self._prev.get("scene", "unknown")
        new_scene = self._current.get("scene", "unknown")
        if self._prev and prev_scene != new_scene:
            delta.scene_changed = True
            delta.prev_scene = prev_scene
            delta.new_scene = new_scene

        # Combat / death transitions
        pp = self._prev.get("player", {})
        cp = self.player
        was_combat, in_combat = pp.get("in_combat", False), cp.get("in_combat", False)
        was_dead,   is_dead   = pp.get("is_dead", False),   cp.get("is_dead", False)
        if not was_combat and in_combat:
            delta.combat_entered = True
        if was_combat and not in_combat:
            delta.combat_exited = True
        if not was_dead and is_dead:
            delta.death_occurred = True

        # Stat deltas
        for stat, attr in [("health","health_delta"), ("food","food_delta"), ("drink","drink_delta")]:
            mx = cp.get(f"max_{stat}", 100)
            if mx > 0:
                setattr(delta, attr, (cp.get(stat, 0) - pp.get(stat, 0)) / mx)

        # Critical stats
        for stat in ("health", "food", "drink", "sleep"):
            val = cp.get(stat, 0)
            mx  = cp.get(f"max_{stat}", 100)
            prev_val = pp.get(stat, 0)
            prev_mx  = pp.get(f"max_{stat}", 100)
            if mx > 0 and val / mx < STAT_CRITICAL_THRESHOLD:
                # Only fire if it wasn't already critical last tick
                if prev_mx <= 0 or prev_val / prev_mx >= STAT_CRITICAL_THRESHOLD:
                    delta.stat_critical.append(stat)

        # Nearby interactions changed
        prev_uids = {i.get("uid") for i in prev_interactions}
        new_uids  = {i.get("uid") for i in self._nearby_interactions}
        if self._prev and prev_uids != new_uids:
            delta.nearby_interactions_changed = True

        # Screen message changed
        if new_screen and new_screen != self._screen_message:
            delta.screen_message_changed = True
            delta.new_screen_message = new_screen

        return delta

    def _mark_blocked(self, x: float, z: float) -> None:
        cell = (int(x) // 5, int(z) // 5)
        if cell not in self._blocked:
            if len(self._blocked_order) == self._blocked_order.maxlen:
                # deque is full — evict the oldest cell from the set
                evicted = self._blocked_order[0]  # leftmost = oldest
                self._blocked.discard(evicted)
            self._blocked_order.append(cell)
            self._blocked.add(cell)
