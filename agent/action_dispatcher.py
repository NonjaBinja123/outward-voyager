"""
ActionDispatcher — executes LLM-returned action sequences against the game.

Receives a list of action dicts from the LLM response and runs them in order.
No game knowledge. No decision-making. Pure execution and result reporting.

Supported actions mirror the universal WebSocket protocol (PROTOCOL.md).
"""
import asyncio
import logging
import math
import random
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

    def __init__(self, game_client: Any, state_manager: Any, executor: Any = None) -> None:
        self._client = game_client
        self._state = state_manager
        self._executor = executor  # SandboxExecutor — optional, enables skill actions
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
                # Support both flat {x,y,z} and nested {position:{x,y,z}}
                pos = params.get("position", params)
                if "x" not in pos or "z" not in pos:
                    raise ValueError(
                        f"navigate_to requires x and z coordinates, got: {params}"
                    )
                x, z = float(pos["x"]), float(pos["z"])
                # Hard-block navigation to known-unreachable cells
                if self._state.is_blocked(x, z):
                    # Redirect to a random unblocked direction instead of doing nothing
                    p = self._state.player
                    px, pz = float(p.get("pos_x", x)), float(p.get("pos_z", z))
                    for _ in range(12):  # try 12 random angles
                        angle = random.uniform(0, 360)
                        dist = random.uniform(15, 40)
                        rx = px + math.cos(math.radians(angle)) * dist
                        rz = pz + math.sin(math.radians(angle)) * dist
                        if not self._state.is_blocked(rx, rz):
                            logger.info(f"[Dispatcher] Blocked target ({x:.0f},{z:.0f}) → random walk ({rx:.0f},{rz:.0f})")
                            await c.navigate_to(rx, float(p.get("pos_y", 0)), rz)
                            self._state.set_navigating(rx, rz)
                            return
                    logger.warning(f"[Dispatcher] All random directions blocked — staying put")
                    return
                await c.navigate_to(x, float(pos.get("y", 0)), z)
                self._state.set_navigating(x, z)  # enable wait_for_arrival polling

            case "wait_for_arrival":
                await self._wait_for_arrival(
                    timeout=float(params.get("timeout", 60.0))
                )

            case "stop_navigation":
                await c.navigate_cancel()

            case "move":
                await c.move(
                    params.get("direction", "forward"),
                    float(params.get("duration", 0.5)),
                )

            # ── Camera ────────────────────────────────────────────────────
            case "look_direction":
                # face_point is the closest equivalent: point camera at world coords
                if "x" in params and "z" in params:
                    await c.face_point(
                        float(params["x"]),
                        float(params.get("y", 0.0)),
                        float(params["z"]),
                    )

            # ── Interaction ───────────────────────────────────────────────
            case "trigger_interaction":
                await c.trigger_interaction(params.get("uid", ""))

            case "take_item":
                await c.take_item(
                    name=params.get("item_name", params.get("name", "")),
                )

            case "open_menu":
                await c.open_menu(params.get("menu", "inventory"))

            case "close_menu":
                await c.close_menu()

            case "game_action":
                await c.game_action(
                    name=params.get("name", ""),
                    mode=params.get("mode", "pulse"),
                )

            case "press_key":
                await c.press_key(params.get("key", ""))

            case "use_item":
                await c.use_item(params.get("item_name", ""))

            case "equip_item":
                await c.equip_item(params.get("item_name", ""))

            case "drop_item":
                logger.warning(f"[Dispatcher] drop_item not supported by game client, skipping")

            # ── Chat / social ─────────────────────────────────────────────
            case "say":
                text = params.get("text") or params.get("message", "")
                if text:
                    await c.say(text)

            # ── Waiting ───────────────────────────────────────────────────
            case "wait":
                secs = params.get("seconds", params.get("duration", 1.0))
                await asyncio.sleep(float(secs))

            case "wait_for_state":
                await self._wait_for_state(
                    key=params.get("key", ""),
                    value=params.get("value"),
                    timeout=float(params.get("timeout", 30.0)),
                )

            # ── Skills ────────────────────────────────────────────────────
            case "execute_skill":
                await self._run_skill(params.get("name", ""))

            case "write_skill" | "rewrite_skill":
                await self._write_skill(
                    name=params.get("name", ""),
                    code=params.get("code", ""),
                    description=params.get("description", ""),
                    is_rewrite=(action == "rewrite_skill"),
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

    async def _run_skill(self, name: str) -> None:
        """Execute a sandboxed skill by name using the async executor."""
        if not self._executor:
            raise ValueError("execute_skill requires a SandboxExecutor — not configured")
        if not name:
            raise ValueError("execute_skill: 'name' param is required")
        from sandbox.executor import SkillContext
        ctx = SkillContext(game_client=self._client, state_manager=self._state)
        result = await self._executor.execute_async(name, "run", ctx)
        if not result.ok:
            raise RuntimeError(f"Skill '{name}' failed: {result.error}")
        logger.info(f"[Dispatcher] Skill '{name}' completed in {result.duration_ms:.0f}ms")

    async def _write_skill(self, name: str, code: str, description: str, is_rewrite: bool) -> None:
        """Validate and integrate agent-written skill code."""
        if not self._executor:
            raise ValueError("write_skill requires a SandboxExecutor — not configured")
        if not name or not code:
            raise ValueError("write_skill: 'name' and 'code' params are required")
        verb = "Rewriting" if is_rewrite else "Writing"
        logger.info(f"[Dispatcher] {verb} skill '{name}'")
        result = self._executor.propose(name, code, description)
        if not result.ok:
            raise RuntimeError(
                f"Skill '{name}' failed validation ({result.stage}): {result.reason}"
            )
        logger.info(f"[Dispatcher] Skill '{name}' integrated successfully")

    async def _notify(
        self, callbacks: list[CompletionCallback], action: str, result: dict | None
    ) -> None:
        for cb in callbacks:
            try:
                await cb(action, result)
            except Exception as e:
                logger.warning(f"[Dispatcher] Callback error: {e}")
