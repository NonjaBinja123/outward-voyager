"""
ActionDispatcher — executes LLM-returned action sequences against the game.

Receives a list of action dicts from the LLM response and runs them in order.
No game knowledge. No decision-making. Pure execution and result reporting.

Supported actions mirror the universal WebSocket protocol (PROTOCOL.md).
"""
import asyncio
import logging
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# Type for completion callback
CompletionCallback = Callable[[str, dict | None], Coroutine[Any, Any, None]]


class ActionDispatcher:
    """
    Executes a sequence of action dicts returned by the LLM.

    Each action has the shape:
        {"action": "<name>", "params": {...}}

    Completion is reported via a registered async callback so the EventBus
    can fire action_completed / action_failed events without circular imports.
    """

    def __init__(self, game_client: Any, state_manager: Any) -> None:
        self._client = game_client
        self._state = state_manager
        self._on_completed: list[CompletionCallback] = []
        self._on_failed: list[CompletionCallback] = []
        self._current_task: asyncio.Task | None = None

    # ── Registration ─────────────────────────────────────────────────────────

    def on_completed(self, cb: CompletionCallback) -> None:
        self._on_completed.append(cb)

    def on_failed(self, cb: CompletionCallback) -> None:
        self._on_failed.append(cb)

    # ── Dispatch ─────────────────────────────────────────────────────────────

    def dispatch(self, actions: list[dict]) -> None:
        """
        Schedule an action sequence for execution.
        Cancels any currently running sequence first.
        """
        if self._current_task and not self._current_task.done():
            logger.debug("[Dispatcher] Cancelling previous action sequence")
            self._current_task.cancel()

        self._current_task = asyncio.get_event_loop().create_task(
            self._run_sequence(actions)
        )

    # ── Execution ─────────────────────────────────────────────────────────────

    async def _run_sequence(self, actions: list[dict]) -> None:
        for step in actions:
            action = step.get("action", "")
            params = step.get("params", {})
            try:
                logger.info(f"[Dispatcher] Execute: {action} {params}")
                await self._execute(action, params)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[Dispatcher] Action failed: {action} — {e}")
                await self._notify(self._on_failed, action, {"error": str(e)})
                return

        last = actions[-1].get("action", "sequence") if actions else "noop"
        await self._notify(self._on_completed, last, None)

    async def _execute(self, action: str, params: dict) -> None:
        """Route a single action to the appropriate game_client call."""
        c = self._client

        match action:
            # ── Movement ──────────────────────────────────────────────────
            case "navigate_to":
                await c.navigate_to(
                    float(params["x"]),
                    float(params.get("y", 0)),
                    float(params["z"]),
                )

            case "wait_for_arrival":
                await self._wait_for_arrival(
                    timeout=float(params.get("timeout", 60.0))
                )

            case "stop_navigation":
                await c.stop_navigation()

            case "move":
                await c.move(
                    params.get("direction", "forward"),
                    float(params.get("duration", 0.5)),
                )

            # ── Camera ────────────────────────────────────────────────────
            case "look_direction":
                await c.look_direction(
                    float(params.get("horizontal", 0.0)),
                    float(params.get("vertical", 0.0)),
                    float(params.get("duration", 0.3)),
                )

            # ── Interaction ───────────────────────────────────────────────
            case "trigger_interaction":
                await c.trigger_interaction(params.get("uid", ""))

            case "press_key":
                await c.press_key(params.get("key", ""))

            case "use_item":
                await c.use_item(params.get("item_name", ""))

            case "equip_item":
                await c.equip_item(params.get("item_name", ""))

            case "drop_item":
                await c.drop_item(params.get("item_name", ""))

            # ── Chat / social ─────────────────────────────────────────────
            case "say":
                await c.say(params.get("text", ""))

            # ── Waiting ───────────────────────────────────────────────────
            case "wait":
                await asyncio.sleep(float(params.get("seconds", 1.0)))

            case "wait_for_state":
                await self._wait_for_state(
                    key=params.get("key", ""),
                    value=params.get("value"),
                    timeout=float(params.get("timeout", 30.0)),
                )

            # ── Unknown ───────────────────────────────────────────────────
            case _:
                logger.warning(f"[Dispatcher] Unknown action: {action!r}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _wait_for_arrival(self, timeout: float = 60.0) -> None:
        """Block until navigation finishes or times out."""
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(0.5)
            elapsed += 0.5
            if not self._state.is_navigating:
                return
        logger.warning("[Dispatcher] wait_for_arrival timed out")

    async def _wait_for_state(self, key: str, value: Any, timeout: float = 30.0) -> None:
        """Block until state[key] == value or timeout."""
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(0.5)
            elapsed += 0.5
            player = self._state.player
            current_val = self._state.current.get(key) or player.get(key)
            if current_val == value:
                return
        logger.warning(f"[Dispatcher] wait_for_state timed out: {key}={value}")

    async def _notify(
        self, callbacks: list[CompletionCallback], action: str, result: dict | None
    ) -> None:
        for cb in callbacks:
            try:
                await cb(action, result)
            except Exception as e:
                logger.warning(f"[Dispatcher] Callback error: {e}")
