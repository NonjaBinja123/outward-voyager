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

# Maps provider config name → API key env var + backend type
# Multiple config entries can share the same key (e.g., claude/claude_haiku/claude_opus)
_API_KEY_VARS: dict[str, str] = {
    "claude":       "ANTHROPIC_API_KEY",
    "claude_haiku": "ANTHROPIC_API_KEY",
    "claude_opus":  "ANTHROPIC_API_KEY",
    "openai":       "OPENAI_API_KEY",
    "openai_full":  "OPENAI_API_KEY",
    "gemini":       "GOOGLE_API_KEY",
    "gemini_lite":  "GOOGLE_API_KEY",
    "gemini_pro":   "GOOGLE_API_KEY",
}

# Maps config name → which backend implementation to use
_BACKEND: dict[str, str] = {
    "claude":        "claude",
    "claude_haiku":  "claude",
    "claude_opus":   "claude",
    "openai":        "openai",
    "openai_full":   "openai",
    "gemini":        "gemini",
    "gemini_lite":   "gemini",
    "gemini_pro":    "gemini",
    "ollama":        "ollama",
    "ollama_vision": "ollama",   # same Ollama backend, vision-capable model
}

# Cost per 1K tokens (input, output) in USD — for tracking only
# Sources: anthropic.com/pricing, openai.com/pricing, ai.google.dev/pricing (2026-03)
_COST_PER_1K: dict[str, tuple[float, float]] = {
    "ollama":       (0.0,     0.0),      # Free — local inference
    "claude_opus":  (0.005,   0.025),    # Opus 4.6: $5/$25 per MTok
    "claude":       (0.003,   0.015),    # Sonnet 4.6: $3/$15 per MTok
    "claude_haiku": (0.001,   0.005),    # Haiku 4.5: $1/$5 per MTok
    "openai":       (0.00025, 0.0006),   # GPT-4o-mini: $0.15/$0.60 per MTok
    "openai_full":  (0.0025,  0.010),    # GPT-4o: $2.50/$10 per MTok
    "gemini":       (0.0003,  0.0025),   # Gemini 2.5 Flash: $0.30/$2.50 per MTok
    "gemini_lite":  (0.0001,  0.0004),   # Gemini 2.5 Flash-Lite: $0.10/$0.40 per MTok
    "gemini_pro":   (0.00125, 0.010),    # Gemini 2.5 Pro: $1.25/$10 per MTok
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

        # Build set of available providers based on config + API key presence
        self._available: set[str] = set()
        for name, cfg in self._providers.items():
            if not cfg.get("enabled", False):
                continue
            if name == "ollama":
                self._available.add(name)
            else:
                key_var = _API_KEY_VARS.get(name, "")
                key_val = os.environ.get(key_var, "") if key_var else ""
                if key_val and len(key_val) > 20:  # reject obvious placeholders
                    self._available.add(name)
                else:
                    logger.info(f"Provider '{name}' enabled but {key_var} not set — skipping")

        # Task routing: task name → ordered list of providers to try
        self._task_routing: dict[str, list[str]] = config.get("task_routing", {})

        # Round-robin index (per task) for providers at the same priority level
        self._rr_index: dict[str, int] = {}

        available_sorted = sorted(self._available)
        logger.info(f"LLM router initialized. Available: {available_sorted}")
        self._load_usage()

    def _get_stats(self, name: str) -> ProviderStats:
        if name not in self._stats:
            self._stats[name] = ProviderStats()
        return self._stats[name]

    # ── Public API ──────────────────────────────────────────────────────────

    async def complete_vision(
        self,
        system: str,
        user: str,
        img_bytes: bytes,
        task: str = "vision",
        max_tokens: int = 1024,
    ) -> str:
        """Route a vision completion (text + image) to the best available provider.

        Task "vision" prefers gemini → ollama_vision → claude_haiku by default.
        Falls back to the text-only complete() if no vision provider succeeds.
        """
        import base64
        img_b64 = base64.b64encode(img_bytes).decode()

        providers = self._select_providers(task)
        for name in providers:
            cfg = self._providers.get(name, {})
            stats = self._get_stats(name)

            if stats.consecutive_failures >= 3:
                cooldown = min(300, 30 * stats.consecutive_failures)
                if time.time() - stats.last_failure < cooldown:
                    continue
            if name not in ("ollama", "ollama_vision") and stats.calls_this_minute >= 50:
                continue

            backend = _BACKEND.get(name, name)
            if backend not in ("gemini", "ollama", "claude"):
                continue  # backend doesn't support vision

            try:
                response = await self._call_vision(name, cfg, backend, system, user, img_b64, img_bytes, max_tokens)
                # Images count roughly as 300 tokens (Gemini) to 1500 (Claude) — use 500 as estimate
                stats.record_call((len(system) + len(user)) // 4 + 500, len(response) // 4)
                logger.info(f"LLM [{task}/vision] response from {name} ({len(response)} chars)")
                return response
            except Exception as e:
                stats.record_failure()
                logger.warning(f"LLM vision provider {name} failed: {e}, trying next...")

        logger.error(f"All vision LLM providers failed for task={task}")
        return ""

    async def _call_vision(
        self, name: str, cfg: dict, backend: str,
        system: str, user: str, img_b64: str, img_bytes: bytes, max_tokens: int,
    ) -> str:
        match backend:
            case "gemini":
                return await self._gemini_vision(cfg, system, user, img_bytes, max_tokens)
            case "ollama":
                return await self._ollama_vision(cfg, system, user, img_b64, max_tokens)
            case "claude":
                return await self._claude_vision(cfg, system, user, img_b64, max_tokens)
            case _:
                raise ValueError(f"Backend '{backend}' does not support vision")

    async def _gemini_vision(self, cfg: dict, system: str, user: str, img_bytes: bytes, max_tokens: int) -> str:
        from google import genai
        from google.genai import types
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        client = genai.Client(api_key=api_key)
        model_name = cfg.get("model", "gemini-2.5-flash")
        image_part = types.Part.from_bytes(data=img_bytes, mime_type="image/png")
        text_part = types.Part(text=user)
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model=model_name,
            contents=[image_part, text_part],
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )
        text = resp.text
        if not text:
            raise ValueError("Gemini returned empty vision response")
        return text

    async def _ollama_vision(self, cfg: dict, system: str, user: str, img_b64: str, max_tokens: int) -> str:
        import aiohttp
        url = cfg.get("base_url", "http://localhost:11434")
        model = cfg.get("model", "moondream2")
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user, "images": [img_b64]},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{url}/api/chat", json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as r:
                r.raise_for_status()
                data = await r.json()
                return data["message"]["content"]

    async def _claude_vision(self, cfg: dict, system: str, user: str, img_b64: str, max_tokens: int) -> str:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        client = anthropic.AsyncAnthropic(api_key=api_key)
        msg = await client.messages.create(
            model=cfg.get("model", "claude-haiku-4-5-20251001"),
            max_tokens=max_tokens,
            system=system,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                    {"type": "text", "text": user},
                ],
            }],
        )
        return msg.content[0].text

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
            cost_in, cost_out = _COST_PER_1K.get(name, _COST_PER_1K.get(_BACKEND.get(name, ""), (0, 0)))
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

        Uses the task_routing config list in order, skipping unavailable providers.
        Ollama is always appended last as the unconditional fallback.
        """
        ordered = self._task_routing.get(task, [])
        if not ordered:
            # No routing config for this task — use all available cloud providers
            # in round-robin order, ollama last
            cloud = sorted(p for p in self._available if p != "ollama")
            rr = self._rr_index.get(task, 0)
            if cloud:
                cloud = cloud[rr:] + cloud[:rr]
                self._rr_index[task] = (rr + 1) % len(cloud)
            ordered = cloud

        result = [p for p in ordered if p in self._available]
        if "ollama" in self._available and "ollama" not in result:
            result.append("ollama")

        if not result:
            result = ["ollama"] if "ollama" in self._available else []

        return result

    # ── Provider implementations ────────────────────────────────────────────

    async def _call(self, name: str, cfg: dict, system: str, user: str, max_tokens: int) -> str:
        backend = _BACKEND.get(name, name)
        match backend:
            case "ollama":
                return await self._ollama(cfg, system, user, max_tokens)
            case "claude":
                return await self._claude(cfg, system, user, max_tokens)
            case "openai":
                return await self._openai(cfg, system, user, max_tokens)
            case "gemini":
                return await self._gemini(cfg, system, user, max_tokens)
            case _:
                raise ValueError(f"Unknown backend '{backend}' for provider '{name}'")

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
        from google import genai
        from google.genai import types
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        client = genai.Client(api_key=api_key)
        model_name = cfg.get("model", "gemini-2.5-flash")
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model=model_name,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )
        text = resp.text
        if not text:
            raise ValueError("Gemini returned empty response")
        return text

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
