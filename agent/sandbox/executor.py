"""
Sandboxed code execution for agent self-written skills.

The agent proposes new Python functions. This module:
  1. Validates the code via CodeValidator
  2. Integrates it into agent/sandbox/skills/ (agent's own code space)
  3. Loads and runs it in-process (post-validation)
  4. Records success/failure, prunes on repeated failure

All integrated code lives under agent/sandbox/skills/ and is
importable as sandbox.skills.<name>. The agent can call async functions
from this namespace via execute_async(name, "run", ctx).

Skill format:
    async def run(ctx: SkillContext) -> None:
        await ctx.game_action("attack")
        await ctx.wait(0.3)

Self-modification log is appended to data/self_modifications.jsonl
so every change is observable for research.
"""
import asyncio
import importlib.util
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sandbox.validator import CodeValidator, ValidationResult

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent / "skills"
_MOD_LOG = Path("./data/self_modifications.jsonl")


class SkillContext:
    """
    Passed to every skill's run(ctx) function.
    Provides game interaction primitives — the only interface a skill needs.

    Skills MUST NOT import game modules or call anything outside ctx.
    The validator enforces banned imports; ctx provides everything safe.
    """

    def __init__(self, game_client: Any, state_manager: Any) -> None:
        self._client = game_client
        self._state = state_manager

    @property
    def state(self) -> dict[str, Any]:
        """Current full game state snapshot."""
        return self._state.current

    @property
    def player(self) -> dict[str, Any]:
        """Shortcut to player sub-dict."""
        return self._state.player

    async def game_action(self, name: str, mode: str = "pulse") -> None:
        """Send a game input. mode: pulse (one frame) | hold | release."""
        await self._client.game_action(name, mode)

    async def wait(self, seconds: float) -> None:
        """Pause execution without blocking the event loop."""
        await asyncio.sleep(seconds)

    async def use_item(self, item_name: str) -> None:
        """Use an item from inventory by name."""
        await self._client.use_item(item_name)

    async def navigate_to(self, x: float, y: float, z: float) -> None:
        """Start pathfinding navigation to world coordinates."""
        await self._client.navigate_to(x, y, z)

    async def trigger_interaction(self, uid: str) -> None:
        """Trigger interaction with a nearby object by its UID."""
        await self._client.trigger_interaction(uid)

    async def say(self, text: str) -> None:
        """Send a message in in-game chat."""
        await self._client.say(text)


@dataclass
class ExecutionResult:
    ok: bool
    return_value: Any = None
    error: str = ""
    duration_ms: float = 0.0


@dataclass
class IntegrationRecord:
    name: str
    timestamp: float
    validation_stage: str
    integrated: bool
    reason: str = ""
    code_hash: str = ""
    times_called: int = 0
    times_succeeded: int = 0


class SandboxExecutor:
    def __init__(self, data_dir: str = "./data") -> None:
        _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        # Ensure skills package is importable
        init = _SKILLS_DIR / "__init__.py"
        if not init.exists():
            init.write_text("")

        self._validator = CodeValidator(smoke_timeout=5.0)
        self._records: dict[str, IntegrationRecord] = {}
        self._log_path = Path(data_dir) / "self_modifications.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_records()

    # ── Public API ──────────────────────────────────────────────────────────

    def propose(self, name: str, code: str, description: str = "") -> ValidationResult:
        """
        Validate and integrate new agent-written code.
        Returns the ValidationResult (ok=True means code was integrated).
        """
        import hashlib
        code_hash = hashlib.sha256(code.encode()).hexdigest()[:12]

        result = self._validator.validate(code, skill_name=name)
        record = IntegrationRecord(
            name=name,
            timestamp=time.time(),
            validation_stage=result.stage,
            integrated=result.ok,
            reason=result.reason,
            code_hash=code_hash,
        )

        if result.ok:
            self._write_skill_file(name, code)
            self._load_module(name)
            logger.info(f"[Sandbox] Integrated '{name}' (hash={code_hash})")
        else:
            logger.warning(f"[Sandbox] Rejected '{name}': {result.reason}")

        self._records[name] = record
        self._log_modification(record, code, description)
        return result

    async def execute_async(self, name: str, fn_name: str, *args: Any, **kwargs: Any) -> ExecutionResult:
        """
        Await fn_name from the integrated skill module named 'name'.
        Use this for async skill functions (the standard format).
        """
        mod = self._get_module(name)
        if mod is None:
            return ExecutionResult(ok=False, error=f"Module '{name}' not integrated")

        fn = getattr(mod, fn_name, None)
        if fn is None:
            return ExecutionResult(ok=False, error=f"Function '{fn_name}' not found in '{name}'")

        t0 = time.perf_counter()
        try:
            rv = await fn(*args, **kwargs)
            elapsed = (time.perf_counter() - t0) * 1000
            if name in self._records:
                self._records[name].times_called += 1
                self._records[name].times_succeeded += 1
            return ExecutionResult(ok=True, return_value=rv, duration_ms=elapsed)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            if name in self._records:
                self._records[name].times_called += 1
            logger.warning(f"[Sandbox] {name}.{fn_name} raised: {e}")
            return ExecutionResult(ok=False, error=str(e), duration_ms=elapsed)

    def discover(self) -> list[str]:
        """
        Scan the skills directory and register any .py files not yet tracked.
        Used on startup to pick up hand-written seed skills without running validation.
        """
        found = []
        for path in sorted(_SKILLS_DIR.glob("*.py")):
            name = path.stem
            if name == "__init__" or name in self._records:
                continue
            self._records[name] = IntegrationRecord(
                name=name,
                timestamp=path.stat().st_mtime,
                validation_stage="builtin",
                integrated=True,
                reason="discovered from file",
            )
            self._load_module(name)
            found.append(name)
            logger.info(f"[Sandbox] Discovered skill: {name!r}")
        return found

    def performance_summary(self) -> str:
        """One-line summary of all skill outcomes for LLM context injection."""
        parts = []
        for rec in self._records.values():
            if not rec.integrated:
                continue
            if rec.times_called == 0:
                parts.append(f"{rec.name}(unused)")
            else:
                rate = rec.times_succeeded / rec.times_called
                parts.append(f"{rec.name}({rec.times_called}x, {rate:.0%}ok)")
        return ", ".join(parts) if parts else "none"

    def execute(self, name: str, fn_name: str, *args: Any, **kwargs: Any) -> ExecutionResult:
        """
        Call fn_name from the integrated skill module named 'name'.
        Records success/failure for pruning decisions.
        """
        mod = self._get_module(name)
        if mod is None:
            return ExecutionResult(ok=False, error=f"Module '{name}' not integrated")

        fn = getattr(mod, fn_name, None)
        if fn is None:
            return ExecutionResult(ok=False, error=f"Function '{fn_name}' not found in '{name}'")

        t0 = time.perf_counter()
        try:
            rv = fn(*args, **kwargs)
            elapsed = (time.perf_counter() - t0) * 1000
            if name in self._records:
                self._records[name].times_called += 1
                self._records[name].times_succeeded += 1
            return ExecutionResult(ok=True, return_value=rv, duration_ms=elapsed)
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            if name in self._records:
                self._records[name].times_called += 1
            logger.warning(f"[Sandbox] {name}.{fn_name} raised: {e}")
            return ExecutionResult(ok=False, error=str(e), duration_ms=elapsed)

    def prune(self, min_calls: int = 3, max_failure_rate: float = 0.4) -> list[str]:
        """Remove skills that fail too often. Returns list of pruned names."""
        pruned = []
        for name, rec in list(self._records.items()):
            if not rec.integrated or rec.times_called < min_calls:
                continue
            failure_rate = 1.0 - (rec.times_succeeded / rec.times_called)
            if failure_rate > max_failure_rate:
                self._remove_skill(name)
                pruned.append(name)
                logger.info(f"[Sandbox] Pruned '{name}' (failure rate={failure_rate:.0%})")
        return pruned

    def list_skills(self) -> list[IntegrationRecord]:
        return [r for r in self._records.values() if r.integrated]

    # ── Internals ───────────────────────────────────────────────────────────

    def _write_skill_file(self, name: str, code: str) -> None:
        path = _SKILLS_DIR / f"{name}.py"
        path.write_text(code, encoding="utf-8")

    def _load_module(self, name: str) -> None:
        module_name = f"sandbox.skills.{name}"
        path = _SKILLS_DIR / f"{name}.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            return
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

    def _get_module(self, name: str):
        module_name = f"sandbox.skills.{name}"
        if module_name not in sys.modules:
            path = _SKILLS_DIR / f"{name}.py"
            if path.exists():
                self._load_module(name)
        return sys.modules.get(module_name)

    def _remove_skill(self, name: str) -> None:
        path = _SKILLS_DIR / f"{name}.py"
        path.unlink(missing_ok=True)
        sys.modules.pop(f"sandbox.skills.{name}", None)
        self._records.pop(name, None)

    def _load_records(self) -> None:
        if not self._log_path.exists():
            return
        with self._log_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("integrated"):
                        rec = IntegrationRecord(
                            name=entry["name"],
                            timestamp=entry["timestamp"],
                            validation_stage=entry.get("stage", "?"),
                            integrated=True,
                            code_hash=entry.get("code_hash", ""),
                        )
                        self._records[rec.name] = rec
                except Exception:
                    pass

    def _log_modification(self, rec: IntegrationRecord, code: str, description: str) -> None:
        entry = {
            "name": rec.name,
            "timestamp": rec.timestamp,
            "stage": rec.validation_stage,
            "integrated": rec.integrated,
            "reason": rec.reason,
            "code_hash": rec.code_hash,
            "description": description,
            "code_length": len(code),
        }
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
