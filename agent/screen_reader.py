"""
ScreenReader — captures the game window and extracts all visible information
via a vision LLM (qwen2.5vl:7b local, or Gemini/Claude when router is enabled).

Capture strategy:
  Primary:  dxcam — GPU-direct DXGI capture, game-window only, ~1ms
  Fallback: mss — DXGI monitor capture if dxcam fails
  NOT used: PIL.ImageGrab — captures desktop compositor, breaks behind windows

Frame differencing:
  Captures a frame every ~1s (cheap). Only sends to vision LLM when
  >DIFF_THRESHOLD% of pixels changed vs. the previous frame.
  Avoids LLM calls when the screen is static.

Used for:
  1. All gameplay decisions — brain.think() calls capture_frame() before every LLM call
  2. Loading screen tips (stored to journal)
  3. Interaction prompts → keybinding learner
  4. Death/respawn screen handling
  5. On-demand via request_vision from LLM response
"""
import asyncio
import logging
import time
from io import BytesIO
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm_router import LLMRouter

logger = logging.getLogger(__name__)

# Minimum pixel-change fraction to trigger a vision LLM call (0.0–1.0)
DIFF_THRESHOLD = 0.05  # 5% of pixels changed

# Target capture size — balances detail vs. token cost
CAPTURE_WIDTH  = 1280
CAPTURE_HEIGHT = 720

_SCREEN_PROMPT = """\
You are reading a screenshot of a video game. Read ALL visible content carefully.

Report what you see:
1. Current game state — where is the character, what is happening on screen?
2. Any menus open — inventory, map, character screen, dialogue, etc. List all visible items/options.
3. Interaction prompts — "[F] Pick Up", "Press E to Interact", any on-screen button hints
4. HUD information — health bars, stamina, status icons, any values visible
5. Loading screen tips — text on dark background during loads
6. Death/respawn screens — "You Died", defeat messages, respawn options
7. Transition prompts — "Press Space to Continue", "Press any key"
8. Any other text, dialogue subtitles, or notifications

Respond with ONLY valid JSON (no markdown fences):
{
  "scene_description": "brief description of what is visible on screen",
  "is_loading_screen": false,
  "is_death_screen": false,
  "menu_open": null,
  "menu_items": [],
  "action_required": false,
  "required_key": "",
  "interaction_hints": [{"key": "f", "action": "pick_up"}],
  "tips": [],
  "notifications": [],
  "all_text": "full verbatim transcript of all visible text"
}

menu_open: null if no menu, or one of: "inventory", "equipment", "map", "skills",
           "character", "crafting", "quest", "dialogue", "settings", "other"
menu_items: list of visible menu entries/options (for inventory: item names; for dialogue: options)
action_required: true when a full-screen overlay requires a key press to continue
required_key: the key to press (lowercase: "space", "e", "f", "tab", etc.)
"""


class ScreenReader:
    """
    Captures the game window and extracts visual information via vision LLM.

    capture_frame() — fast GPU capture, returns raw bytes (no LLM)
    read_screen()   — capture + vision LLM parse, with frame diff check
    force_read()    — capture + vision LLM parse, ignoring diff threshold
    """

    def __init__(self, llm: "LLMRouter") -> None:
        self._llm = llm
        self._seen_tips: set[str] = set()
        self._last_frame: bytes | None = None
        self._last_read_time: float = 0.0
        self._camera: Any = None        # dxcam camera (lazy init)
        self._camera_failed: bool = False
        self._game_title = "Outward Definitive Edition"

    # ── Frame capture ─────────────────────────────────────────────────────────

    def capture_frame(self) -> bytes | None:
        """
        Capture the game window as JPEG bytes.
        Fast (~1-5ms). No LLM involved.
        Returns None if capture fails.
        """
        try:
            return self._capture_dxcam() or self._capture_mss()
        except Exception as e:
            logger.warning(f"[Screen] capture_frame failed: {e}")
            return None

    def _capture_dxcam(self) -> bytes | None:
        """GPU-direct DXGI capture via dxcam."""
        try:
            import dxcam
            if self._camera is None and not self._camera_failed:
                try:
                    self._camera = dxcam.create(output_idx=0, output_color="BGR")
                except Exception as e:
                    logger.warning(f"[Screen] dxcam init failed: {e}")
                    self._camera_failed = True
                    return None

            if self._camera_failed:
                return None

            frame = self._camera.grab()
            if frame is None:
                return None

            # Convert numpy BGR array → JPEG bytes via PIL
            from PIL import Image
            import numpy as np
            img = Image.fromarray(frame[..., ::-1])  # BGR → RGB
            img.thumbnail((CAPTURE_WIDTH, CAPTURE_HEIGHT))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except Exception as e:
            logger.debug(f"[Screen] dxcam capture failed: {e}")
            return None

    def _capture_mss(self) -> bytes | None:
        """mss monitor capture fallback."""
        try:
            import mss
            import mss.tools
            from PIL import Image
            with mss.mss() as sct:
                # Capture primary monitor
                monitor = sct.monitors[1]
                raw = sct.grab(monitor)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                img.thumbnail((CAPTURE_WIDTH, CAPTURE_HEIGHT))
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=85)
                return buf.getvalue()
        except Exception as e:
            logger.debug(f"[Screen] mss capture failed: {e}")
            return None

    # ── Frame differencing ────────────────────────────────────────────────────

    def _significant_change(self, new_frame: bytes) -> bool:
        """Return True if >DIFF_THRESHOLD fraction of pixels changed."""
        if self._last_frame is None:
            return True
        try:
            import numpy as np
            from PIL import Image
            old = np.array(Image.open(BytesIO(self._last_frame)).convert("L"))
            new = np.array(Image.open(BytesIO(new_frame)).convert("L"))
            if old.shape != new.shape:
                return True
            diff = np.abs(old.astype(int) - new.astype(int))
            changed = np.sum(diff > 20) / diff.size  # pixels changed by >20 intensity
            logger.debug(f"[Screen] Frame diff: {changed:.1%}")
            return changed > DIFF_THRESHOLD
        except Exception:
            return True  # on any error, treat as changed

    # ── Vision LLM read ───────────────────────────────────────────────────────

    async def read_screen(self, min_interval: float = 5.0) -> dict[str, Any] | None:
        """
        Capture + vision LLM parse, with frame diff check and interval throttle.
        Returns parsed dict or None if skipped/failed.
        min_interval: minimum seconds between LLM calls (0 to skip throttle).
        """
        now = time.time()
        if min_interval > 0 and now - self._last_read_time < min_interval:
            return None

        frame = await asyncio.to_thread(self.capture_frame)
        if not frame:
            return None

        if not self._significant_change(frame):
            logger.debug("[Screen] Frame unchanged — skipping LLM")
            return None

        return await self._call_vision(frame)

    async def force_read(self) -> dict[str, Any] | None:
        """
        Capture + vision LLM parse regardless of interval or frame diff.
        Use for: on-demand request_vision, death screen, menus just opened.
        """
        frame = await asyncio.to_thread(self.capture_frame)
        if not frame:
            return None
        return await self._call_vision(frame)

    async def _call_vision(self, img_bytes: bytes) -> dict[str, Any] | None:
        """Send frame to vision LLM, parse response."""
        self._last_read_time = time.time()
        self._last_frame = img_bytes

        try:
            import json, re
            raw = await self._llm.complete_vision(
                system="You read game screenshots and report everything visible.",
                user=_SCREEN_PROMPT,
                img_bytes=img_bytes,
                task="vision",
            )
            raw = raw.strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
            data: dict[str, Any] = json.loads(raw)
            scene = data.get("scene_description", "")[:60]
            menu = data.get("menu_open")
            logger.info(f"[Screen] Read OK — scene={scene!r} menu={menu}")
            return data
        except Exception as e:
            logger.warning(f"[Screen] Vision call failed: {e}")
            return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def new_tips(self, data: dict[str, Any]) -> list[str]:
        """Return tips from result that haven't been seen this session."""
        out = []
        for tip in data.get("tips", []):
            tip = tip.strip()
            if tip and tip not in self._seen_tips:
                self._seen_tips.add(tip)
                out.append(tip)
        return out

    def interaction_hints(self, data: dict[str, Any]) -> list[dict[str, str]]:
        """Return [{key, action}, ...] from a result."""
        return [
            h for h in data.get("interaction_hints", [])
            if h.get("key") and h.get("action")
        ]
