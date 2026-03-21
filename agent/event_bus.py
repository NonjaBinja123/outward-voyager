"""
EventBus — decides when the LLM should be consulted.

Receives StateDelta and other signals, fires named events with debouncing.
No game logic. No decisions. Just: did something happen that warrants LLM attention?
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

from state_manager import StateDelta

logger = logging.getLogger(__name__)

# Minimum seconds between any two LLM calls (hard debounce)
DEBOUNCE_SECONDS = 2.0

# If nothing triggers the LLM for this long, fire idle_timeout
IDLE_TIMEOUT_SECONDS = 8.0


@dataclass
class GameEvent:
    """A named event carrying optional payload, ready to fire at the LLM."""
    name: str
    data: dict[str, Any] = field(default_factory=dict)


# Type alias for async event handler
EventHandler = Callable[[GameEvent], Coroutine[Any, Any, None]]


class EventBus:
    """
    Converts raw signals (StateDelta, game messages, external triggers) into
    named GameEvents and calls registered async handlers with debouncing.

    Handlers are called sequentially (first registered, first called).
    At most one LLM call fires every DEBOUNCE_SECONDS.
    An idle_timeout event fires after IDLE_TIMEOUT_SECONDS with no events.
    """

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []
        self._last_fire: float = 0.0
        self._last_activity: float = time.time()
        self._pending: list[GameEvent] = []
        self._idle_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._firing: bool = False  # prevent concurrent LLM calls

    # ── Registration ─────────────────────────────────────────────────────────

    def on_event(self, handler: EventHandler) -> None:
        """Register an async handler. All events are delivered to all handlers."""
        self._handlers.append(handler)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the idle-timeout watcher. Call once the event loop is running."""
        self._loop = asyncio.get_event_loop()
        self._idle_task = self._loop.create_task(self._idle_watcher())

    def stop(self) -> None:
        if self._idle_task:
            self._idle_task.cancel()
            self._idle_task = None

    # ── Signal ingestion ─────────────────────────────────────────────────────

    def on_state_delta(self, delta: StateDelta) -> None:
        """Feed a StateDelta. Translates to zero or more GameEvents."""
        if delta.scene_changed:
            self._enqueue(GameEvent("scene_changed", {
                "prev": delta.prev_scene,
                "new": delta.new_scene,
            }))

        if delta.combat_entered:
            self._enqueue(GameEvent("combat_entered"))

        if delta.combat_exited:
            self._enqueue(GameEvent("combat_exited"))

        if delta.death_occurred:
            self._enqueue(GameEvent("death"))

        if delta.stat_critical:
            self._enqueue(GameEvent("stat_critical", {"stats": delta.stat_critical}))

        if delta.screen_message_changed:
            self._enqueue(GameEvent("screen_hint", {
                "text": delta.new_screen_message,
            }))

        if delta.nearby_interactions_changed:
            self._enqueue(GameEvent("interactions_changed"))

    def on_nav_arrived(self) -> None:
        self._enqueue(GameEvent("nav_arrived"))

    def on_nav_failed(self, reason: str = "") -> None:
        self._enqueue(GameEvent("nav_failed", {"reason": reason}))

    def on_nav_stuck(self, px: float, py: float, pz: float) -> None:
        self._enqueue(GameEvent("nav_stuck", {"x": px, "y": py, "z": pz}))

    def on_player_chat(self, text: str, speaker: str = "") -> None:
        self._enqueue(GameEvent("player_chat", {"text": text, "speaker": speaker}))

    def on_dashboard_chat(self, text: str, speaker: str = "") -> None:
        self._enqueue(GameEvent("dashboard_chat", {"text": text, "speaker": speaker or "Dashboard"}))

    def on_action_failed(self, action: str, reason: str = "") -> None:
        # Do NOT enqueue — action failures should NOT immediately re-trigger the LLM.
        # The agent will recover on the next idle_timeout. This prevents runaway loops
        # where every failed action fires a new LLM call which also fails.
        self._last_activity = time.time()  # reset idle timer so we don't stack failures
        logger.debug(f"[EventBus] Action failed (no LLM trigger): {action} — {reason}")

    def on_action_completed(self, action: str, result: dict | None = None) -> None:
        """Called when an action sequence completes. Resets idle timer; LLM fires on next idle_timeout."""
        # Just reset activity — idle_timeout will drive the next think naturally.
        # This prevents rapid-fire LLM calls after every completed micro-action.
        self._last_activity = time.time()
        logger.debug(f"[EventBus] Action sequence completed: {action}")

    def on_strategy_request(self, reason: str = "") -> None:
        """Agent self-triggers a deep strategy session."""
        self._enqueue(GameEvent("strategy_request", {"reason": reason}))

    def on_screen_read(self, data: dict[str, Any]) -> None:
        """Vision LLM completed a screen read. Only surface during loading screens."""
        # Screen reads are context-only — they don't trigger LLM calls during normal play.
        # The orchestrator stores tips to journal directly; the LLM sees state via game_state.
        self._last_activity = time.time()  # counts as activity so idle timer resets

    # ── Internals ────────────────────────────────────────────────────────────

    def _enqueue(self, event: GameEvent) -> None:
        """Add event to pending queue and schedule dispatch."""
        self._pending.append(event)
        if self._loop and self._loop.is_running():
            self._loop.create_task(self._flush())

    async def _flush(self) -> None:
        """Drain pending events, respecting debounce and preventing concurrent fires."""
        if not self._pending:
            return

        # If already handling an event, wait — don't stack LLM calls
        if self._firing:
            return

        now = time.time()
        wait = DEBOUNCE_SECONDS - (now - self._last_fire)
        if wait > 0:
            await asyncio.sleep(wait)

        if not self._pending or self._firing:
            return

        # Take highest-priority event
        event = self._pending[-1]
        for e in self._pending:
            if e.name == "strategy_request":
                event = e
                break
            if e.name in ("death", "combat_entered", "player_chat", "dashboard_chat"):
                event = e
                break

        self._pending.clear()
        await self._fire(event)

    async def _fire(self, event: GameEvent) -> None:
        self._firing = True
        self._last_fire = time.time()
        self._last_activity = time.time()
        logger.debug(f"[EventBus] Fire: {event.name} {event.data}")
        try:
            for handler in self._handlers:
                try:
                    await handler(event)
                except asyncio.CancelledError:
                    raise  # don't swallow task cancellation
                except Exception as e:
                    logger.warning(f"[EventBus] Handler error on {event.name!r}: {e}")
        finally:
            self._firing = False
            # If events accumulated while we were busy, schedule a flush
            if self._pending and self._loop and self._loop.is_running():
                self._loop.create_task(self._flush())

    async def _idle_watcher(self) -> None:
        """Periodically check if we've been idle too long and fire idle_timeout."""
        while True:
            await asyncio.sleep(1.0)
            if time.time() - self._last_activity >= IDLE_TIMEOUT_SECONDS:
                self._last_activity = time.time()   # reset before fire
                await self._fire(GameEvent("idle_timeout"))
