"""
Multi-provider LLM router with task-based routing, round-robin rotation,
rate limit awareness, and usage tracking. Always has Ollama as the final
fallback (local, no API key needed).

Task types:
  - "chat":     Fast, cheap — player-facing responses (use smallest capable model)
  - "strategy": Medium — decision-making, goal evaluation
  - "code":     Expensive — skill generation, self-modification (use best model)
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Environment variable names for each provider's API key
_API_KEY_VARS: dict[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GOOGLE_API_KEY",
}

# Approximate cost per 1K tokens (input, output) in USD — for tracking only
_COST_PER_1K: dict[str, tuple[float, float]] = {
    "ollama": (0.0, 0.0),
    "claude": (0.015, 0.075),   # Claude Sonnet 4.6
    "openai": (0.005, 0.015),   # GPT-4o
    "gemini": (0.0005, 0.0015), # Gemini 2.0 Flash
}


@dataclass
class ProviderStats:
    """Tracks usage and health per provider."""
    calls: int = 0
    failures: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    last_call: float = 0.0
    last_failure: float = 0.0
    calls_this_minute: int = 0
    minute_start: float = 0.0
    consecutive_failures: int = 0

    @property
    def estimated_cost_usd(self) -> float:
        return 0.0  # Computed by router with provider name

    def record_call(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        now = time.time()
        self.calls += 1
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.last_call = now
        self.consecutive_failures = 0
        # Rate tracking
        if now - self.minute_start > 60:
            self.calls_this_minute = 0
            self.minute_start = now
        self.calls_this_minute += 1

    def record_failure(self) -> None:
        self.failures += 1
        self.last_failure = time.time()
        self.consecutive_failures += 1


class LLMRouter:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._providers = config.get("providers", {})
        self._stats: dict[str, ProviderStats] = {}
        self._usage_path = Path("./data/llm_usage.json")
        self._usage_path.parent.mkdir(parents=True, exist_ok=True)

        # Build available provider list based on config + API keys
        self._available: list[str] = []
        for name in config.get("priority", ["ollama"]):
            if name == "ollama":
                self._available.append(name)
            elif self._providers.get(name, {}).get("enabled", False):
                key_var = _API_KEY_VARS.get(name, "")
                if key_var and os.environ.get(key_var):
                    self._available.append(name)
                else:
                    logger.info(f"LLM provider {name} enabled but {key_var} not set — skipping")
            else:
                logger.debug(f"LLM provider {name} disabled in config")

        # Round-robin index for rotation
        self._rr_index = 0

        # Task-to-provider routing overrides (optional in config)
        self._task_routing: dict[str, str] = config.get("task_routing", {})

        logger.info(f"LLM router initialized. Available providers: {self._available}")
        self._load_usage()

    def _get_stats(self, name: str) -> ProviderStats:
        if name not in self._stats:
            self._stats[name] = ProviderStats()
        return self._stats[name]

    # ── Public API ──────────────────────────────────────────────────────────

    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        task: str = "chat",
    ) -> str:
        """Route a completion request to the best available provider.

        Args:
            system: System prompt.
            user: User message.
            max_tokens: Max output tokens.
            task: Task type — "chat", "strategy", or "code". Affects provider
                  selection when multiple are available.
        """
        providers = self._select_providers(task)

        for name in providers:
            cfg = self._providers.get(name, {})
            stats = self._get_stats(name)

            # Back off if provider is failing repeatedly
            if stats.consecutive_failures >= 3:
                cooldown = min(300, 30 * stats.consecutive_failures)
                if time.time() - stats.last_failure < cooldown:
                    logger.debug(f"Skipping {name} — {stats.consecutive_failures} consecutive failures, cooling down")
                    continue

            # Rate limit guard (conservative: 50 calls/minute for APIs)
            if name != "ollama" and stats.calls_this_minute >= 50:
                logger.debug(f"Skipping {name} — rate limit guard (50/min)")
                continue

            try:
                response = await self._call(name, cfg, system, user, max_tokens)
                # Estimate tokens (rough: 1 token ≈ 4 chars)
                est_input = (len(system) + len(user)) // 4
                est_output = len(response) // 4
                stats.record_call(est_input, est_output)
                logger.info(f"LLM [{task}] response from {name} ({len(response)} chars)")
                return response
            except Exception as e:
                stats.record_failure()
                logger.warning(f"LLM provider {name} failed: {e}, trying next...")

        logger.error(f"All LLM providers failed for task={task}")
        return ""

    def get_usage_summary(self) -> dict[str, Any]:
        """Return usage stats for dashboard/logging."""
        summary: dict[str, Any] = {}
        for name, stats in self._stats.items():
            cost_in, cost_out = _COST_PER_1K.get(name, (0, 0))
            est_cost = (
                stats.total_input_tokens / 1000 * cost_in
                + stats.total_output_tokens / 1000 * cost_out
            )
            summary[name] = {
                "calls": stats.calls,
                "failures": stats.failures,
                "est_input_tokens": stats.total_input_tokens,
                "est_output_tokens": stats.total_output_tokens,
                "est_cost_usd": round(est_cost, 4),
            }
        return summary

    def save_usage(self) -> None:
        """Persist usage stats to disk."""
        try:
            self._usage_path.write_text(
                json.dumps(self.get_usage_summary(), indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Failed to save LLM usage: {e}")

    # ── Provider selection ──────────────────────────────────────────────────

    def _select_providers(self, task: str) -> list[str]:
        """Return ordered list of providers to try for a given task.

        If a task_routing override exists, try that provider first.
        Otherwise use round-robin among cloud providers, with ollama as fallback.
        """
        result: list[str] = []

        # Check for explicit task routing
        preferred = self._task_routing.get(task)
        if preferred and preferred in self._available:
            result.append(preferred)

        # Cloud providers in round-robin order (skip ollama for now)
        cloud = [p for p in self._available if p != "ollama"]
        if cloud:
            for i in range(len(cloud)):
                idx = (self._rr_index + i) % len(cloud)
                name = cloud[idx]
                if name not in result:
                    result.append(name)
            self._rr_index = (self._rr_index + 1) % len(cloud)

        # Ollama always last as fallback
        if "ollama" in self._available and "ollama" not in result:
            result.append("ollama")

        return result

    # ── Provider implementations ────────────────────────────────────────────

    async def _call(self, name: str, cfg: dict, system: str, user: str, max_tokens: int) -> str:
        match name:
            case "ollama":
                return await self._ollama(cfg, system, user, max_tokens)
            case "claude":
                return await self._claude(cfg, system, user, max_tokens)
            case "openai":
                return await self._openai(cfg, system, user, max_tokens)
            case "gemini":
                return await self._gemini(cfg, system, user, max_tokens)
            case _:
                raise ValueError(f"Unknown provider: {name}")

    async def _ollama(self, cfg: dict, system: str, user: str, max_tokens: int) -> str:
        import aiohttp
        url = cfg.get("base_url", "http://localhost:11434")
        model = cfg.get("model", "llama3.1:8b")
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{url}/api/chat", json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as r:
                r.raise_for_status()
                data = await r.json()
                return data["message"]["content"]

    async def _claude(self, cfg: dict, system: str, user: str, max_tokens: int) -> str:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        client = anthropic.AsyncAnthropic(api_key=api_key)
        msg = await client.messages.create(
            model=cfg.get("model", "claude-sonnet-4-6"),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text

    async def _openai(self, cfg: dict, system: str, user: str, max_tokens: int) -> str:
        import openai
        api_key = os.environ.get("OPENAI_API_KEY", "")
        client = openai.AsyncOpenAI(api_key=api_key)
        resp = await client.chat.completions.create(
            model=cfg.get("model", "gpt-4o"),
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    async def _gemini(self, cfg: dict, system: str, user: str, max_tokens: int) -> str:
        import google.generativeai as genai
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            cfg.get("model", "gemini-2.0-flash"),
            system_instruction=system,
        )
        resp = await asyncio.to_thread(
            model.generate_content,
            user,
            generation_config={"max_output_tokens": max_tokens},
        )
        return resp.text

    # ── Persistence ─────────────────────────────────────────────────────────

    def _load_usage(self) -> None:
        """Load previous usage stats if they exist."""
        if not self._usage_path.exists():
            return
        try:
            data = json.loads(self._usage_path.read_text(encoding="utf-8"))
            for name, info in data.items():
                stats = self._get_stats(name)
                stats.calls = info.get("calls", 0)
                stats.failures = info.get("failures", 0)
                stats.total_input_tokens = info.get("est_input_tokens", 0)
                stats.total_output_tokens = info.get("est_output_tokens", 0)
        except Exception as e:
            logger.warning(f"Failed to load LLM usage: {e}")
