"""
ScreenReader — takes screenshots of the game and extracts all visible text
via a vision LLM routed to Gemini Flash or Ollama (cheap/free).

Used for:
  1. Loading screen tips (stored to journal as permanent game knowledge)
  2. Interaction prompts ("[F] Drink", "[E] Interact") → updates keybinding learner
  3. On-screen notifications / status messages
  4. General situational awareness for the orchestrator

Usage:
    reader = ScreenReader(llm_router)
    data = await reader.read_screen()
    new_tips = reader.new_tips(data)       # deduplicated within session
    hints   = reader.interaction_hints(data)  # [{key, action}, ...]
"""
import asyncio
import json
import logging
import re
import time
from io import BytesIO
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm_router import LLMRouter

logger = logging.getLogger(__name__)

_SCREEN_PROMPT = """\
You are reading a screenshot of "Outward Definitive Edition" (fantasy action-RPG).

Read ALL visible text on screen carefully and report what you find.

Focus on:
1. Loading screen tips — white/yellow text on dark background during loads
   Example: "Tip: Food can be cooked at campfires to improve its effects."
2. Interaction prompts — key hints near objects/NPCs
   Examples: "[F] Pick Up", "Press E to Interact", "[E] Drink Water", "F - Open"
3. Notifications and popups — item acquired, status effects, quest updates
4. Any other HUD text, dialogue subtitles, or on-screen messages

Respond with ONLY valid JSON (no markdown fences):
{
  "is_loading_screen": false,
  "tips": ["Tip text here if visible"],
  "interaction_hints": [{"key": "f", "action": "pick_up"}],
  "notifications": ["Item acquired: Iron Sword"],
  "all_text": "full verbatim transcript of all visible text"
}

If the screen is black, a main menu, or nothing useful is visible, return all empty arrays.
Key names should be lowercase single characters or words (e, f, tab, space, etc).
Action names should be short lowercase words (interact, drink, pick_up, open, talk, etc).
"""


class ScreenReader:
    """Reads game screen via vision LLM. Routes to Gemini/Ollama by default (cheap/free)."""

    def __init__(self, llm: "LLMRouter") -> None:
        self._llm = llm
        self._seen_tips: set[str] = set()      # deduplicate within session
        self._last_read_time: float = 0.0

    async def read_screen(self, min_interval: float = 8.0) -> dict[str, Any] | None:
        """Take a screenshot and extract all visible text.

        Returns parsed dict or None if interval not met or screenshot failed.
        min_interval: minimum seconds between reads (0 to force).
        """
        now = time.time()
        if now - self._last_read_time < min_interval:
            return None
        self._last_read_time = now

        img_bytes = await asyncio.to_thread(self._capture_screen)
        if not img_bytes:
            return None

        try:
            raw = await self._llm.complete_vision(
                system="You read game screenshots and extract all visible text.",
                user=_SCREEN_PROMPT,
                img_bytes=img_bytes,
                task="vision",
            )
            raw = raw.strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
            data: dict[str, Any] = json.loads(raw)
            loading = data.get("is_loading_screen", False)
            ntips = len(data.get("tips", []))
            nhints = len(data.get("interaction_hints", []))
            logger.info(f"[Screen] Read OK — loading={loading} tips={ntips} hints={nhints}")
            return data
        except json.JSONDecodeError as e:
            logger.debug(f"[Screen] Vision response not JSON: {e}")
            return None
        except Exception as e:
            logger.warning(f"[Screen] read_screen failed: {e}")
            return None

    def new_tips(self, data: dict[str, Any]) -> list[str]:
        """Return tips from a result that haven't been seen this session."""
        out = []
        for tip in data.get("tips", []):
            tip = tip.strip()
            if tip and tip not in self._seen_tips:
                self._seen_tips.add(tip)
                out.append(tip)
        return out

    def interaction_hints(self, data: dict[str, Any]) -> list[dict[str, str]]:
        """Return interaction hints as [{key, action}, ...] from a result."""
        return [
            h for h in data.get("interaction_hints", [])
            if h.get("key") and h.get("action")
        ]

    def _capture_screen(self) -> bytes | None:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.thumbnail((1280, 720))
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            logger.warning("[Screen] Pillow not installed — vision screen reading disabled")
            return None
        except Exception as e:
            logger.warning(f"[Screen] Screenshot failed: {e}")
            return None
