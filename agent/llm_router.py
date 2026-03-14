"""
Multi-provider LLM router. Tries providers in priority order and auto-rotates
on failure. Always has Ollama as the final fallback (local, no API key needed).
"""
import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class LLMRouter:
    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._priority: list[str] = config.get("priority", ["ollama"])
        self._providers = config.get("providers", {})

    async def complete(self, system: str, user: str, max_tokens: int = 1024) -> str:
        """Try each provider in priority order; return first successful response."""
        for name in self._priority:
            cfg = self._providers.get(name, {})
            if not cfg.get("enabled", False) and name != "ollama":
                continue
            try:
                response = await self._call(name, cfg, system, user, max_tokens)
                logger.info(f"LLM response from {name} ({len(response)} chars)")
                return response
            except Exception as e:
                logger.warning(f"LLM provider {name} failed: {e}, trying next...")
        return ""

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
        model = cfg.get("model", "llama3.2")
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
            async with session.post(f"{url}/api/chat", json=payload, timeout=aiohttp.ClientTimeout(total=60)) as r:
                r.raise_for_status()
                data = await r.json()
                return data["message"]["content"]

    async def _claude(self, cfg: dict, system: str, user: str, max_tokens: int) -> str:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = await client.messages.create(
            model=cfg.get("model", "claude-opus-4-6"),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text

    async def _openai(self, cfg: dict, system: str, user: str, max_tokens: int) -> str:
        import openai
        client = openai.AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
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
        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
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
