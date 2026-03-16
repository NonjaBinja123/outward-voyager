"""
VisionAutoLoader — drives Outward's main menu → load save sequence.

Strategy:
  1. Poll the C# mod for the current menu state (fast, accurate).
  2. Take a screenshot and ask Claude Haiku what's on screen (vision sanity-check).
  3. Based on combined knowledge, send the appropriate C# menu command.
  4. Repeat until in_game state is confirmed.

The C# AutoLoader MonoBehaviour is disabled; this module owns all menu interaction.
"""
import asyncio
import base64
import json
import logging
import os
import re
import time
from io import BytesIO
from typing import Any

logger = logging.getLogger(__name__)

# ── Vision prompt ────────────────────────────────────────────────────────────

VISION_PROMPT = """\
You are watching a screenshot of the PC game "Outward Definitive Edition."

Identify the current screen and respond with ONLY a valid JSON object (no markdown fences):
{
  "screen": "<main_menu | character_select | loading | in_game | unknown>",
  "notes": "<one sentence: what UI elements or text are visible>"
}

Definitions:
- main_menu: title/home screen — "Continue", "New Game", or "Load" buttons visible
- character_select: a list of character portrait cards or names to choose from
- loading: loading bar, spinning icon, or dark transition screen with progress
- in_game: game world is visible — HUD bars, sky, terrain, player character
- unknown: none of the above clearly visible
"""


class VisionAutoLoader:
    """
    Drives Outward's main menu → character select → save load sequence
    using Claude Haiku vision + C# menu commands.
    """

    TARGET_CHARACTER = "AgentNeo"
    POLL_INTERVAL    = 3.0   # seconds between state checks
    LOAD_TIMEOUT     = 120.0 # max seconds to wait for level to finish loading
    VISION_RESIZE    = (1280, 720)

    def __init__(self, game: Any, config: dict) -> None:
        self._game   = game
        self._config = config
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        # Collect any pending menu_state responses
        self._menu_state: dict | None = None
        game.on("menu_state", self._on_menu_state)

    async def _on_menu_state(self, msg: dict) -> None:
        self._menu_state = msg

    # ── Public entry point ───────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Persistent monitor loop. Runs forever — drives the loading sequence
        any time the game is at the main menu, then watches for a return to
        the main menu (failed load, death, etc.) and re-runs automatically.
        """
        logger.info("[AutoLoader] Persistent monitor started.")
        while True:
            try:
                await self._run_once()
            except Exception as e:
                logger.error(f"[AutoLoader] Sequence error: {e}")
            # Back to idle — poll slowly until we see main_menu again
            await self._wait_for_main_menu()

    async def _wait_for_main_menu(self) -> None:
        """Idle loop: poll every 10s until we're back at the main menu."""
        while True:
            await asyncio.sleep(10.0)
            state = await self._query_menu_state()
            screen = state.get("screen", "unknown")
            if screen in ("main_menu", "character_select"):
                logger.info(f"[AutoLoader] Detected {screen} — starting load sequence.")
                return

    async def _run_once(self) -> None:
        """
        Run one full load attempt. Returns when in-game, or raises after timeout.
        """
        logger.info("[AutoLoader] Starting load sequence...")
        deadline = time.monotonic() + self.LOAD_TIMEOUT

        while time.monotonic() < deadline:
            state = await self._query_menu_state()
            screen = state.get("screen", "unknown")
            logger.info(f"[AutoLoader] C# screen={screen}")

            if screen == "in_game":
                logger.info("[AutoLoader] In-game confirmed.")
                return

            elif screen == "main_menu":
                vision = await self._vision_screen()
                logger.info(f"[AutoLoader] Vision says: {vision.get('screen')} — {vision.get('notes','')}")
                if vision.get("screen") in ("main_menu", "unknown"):
                    logger.info("[AutoLoader] Pressing Continue...")
                    await self._game.send("menu_press_continue")
                    await asyncio.sleep(self.POLL_INTERVAL)

            elif screen == "character_select":
                vision = await self._vision_screen()
                logger.info(f"[AutoLoader] Vision says: {vision.get('screen')} — {vision.get('notes','')}")
                characters = state.get("characters", [])
                char_names = [c.get("name", "") for c in characters]
                logger.info(f"[AutoLoader] Characters available: {char_names}")
                if self.TARGET_CHARACTER in char_names:
                    logger.info(f"[AutoLoader] Selecting '{self.TARGET_CHARACTER}'...")
                    await self._game.send("menu_select_character", {"name": self.TARGET_CHARACTER})
                    await asyncio.sleep(2.0)
                    # After selecting character, click the latest save (index 0)
                    logger.info("[AutoLoader] Selecting save slot 0 (latest)...")
                    await self._game.send("menu_select_save", {"index": 0})
                    await asyncio.sleep(self.POLL_INTERVAL)
                else:
                    logger.warning(f"[AutoLoader] '{self.TARGET_CHARACTER}' not in {char_names} — selecting index 0")
                    await self._game.send("menu_select_character", {"name": char_names[0] if char_names else ""})
                    await asyncio.sleep(self.POLL_INTERVAL)

            elif screen == "loading":
                vision = await self._vision_screen()
                logger.info(f"[AutoLoader] Loading... Vision: {vision.get('notes','')}")
                # Check if the load-prompt screen is showing (AllPlayerDoneLoading but not ready)
                all_done  = state.get("all_done_loading", False)
                all_ready = state.get("all_ready", False)
                if all_done and not all_ready:
                    logger.info("[AutoLoader] Load prompt detected — pressing space...")
                    await self._game.send("menu_press_space")
                await asyncio.sleep(self.POLL_INTERVAL)

            else:
                # unknown — ask vision
                vision = await self._vision_screen()
                logger.info(f"[AutoLoader] Unknown state. Vision: {vision.get('screen')} — {vision.get('notes','')}")
                v_screen = vision.get("screen", "unknown")
                if v_screen == "main_menu":
                    await self._game.send("menu_press_continue")
                elif v_screen == "loading":
                    await asyncio.sleep(self.POLL_INTERVAL)
                elif v_screen == "in_game":
                    logger.info("[AutoLoader] Vision confirms in-game. Done.")
                    return
                else:
                    await asyncio.sleep(self.POLL_INTERVAL)

        raise TimeoutError("[AutoLoader] Timed out waiting for game to load.")

    # ── C# menu state ────────────────────────────────────────────────────────

    async def _query_menu_state(self) -> dict:
        """Ask the C# mod what screen is currently showing."""
        self._menu_state = None
        await self._game.send("menu_query_state")
        # Wait up to 3s for the response
        for _ in range(30):
            if self._menu_state is not None:
                return self._menu_state
            await asyncio.sleep(0.1)
        logger.warning("[AutoLoader] menu_query_state timed out — returning empty state")
        return {"screen": "unknown"}

    # ── Vision ───────────────────────────────────────────────────────────────

    async def _vision_screen(self) -> dict:
        """
        Take a screenshot and ask Claude Haiku what screen is showing.
        Returns dict with 'screen' and 'notes' keys.
        Falls back to {'screen': 'unknown'} on any error.
        """
        try:
            img_bytes = await asyncio.to_thread(self._capture_screen)
            if not img_bytes:
                return {"screen": "unknown", "notes": "screenshot failed"}
            return await asyncio.to_thread(self._ask_vision, img_bytes)
        except Exception as e:
            logger.warning(f"[AutoLoader] Vision error: {e}")
            return {"screen": "unknown", "notes": str(e)}

    def _capture_screen(self) -> bytes | None:
        """Capture the primary screen, return PNG bytes."""
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.thumbnail(self.VISION_RESIZE)
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            logger.warning("[AutoLoader] Pillow not installed — vision disabled. Run: pip install Pillow")
            return None

    def _ask_vision(self, img_bytes: bytes) -> dict:
        """Send screenshot to Claude Haiku and parse the JSON response."""
        import anthropic
        if not self._api_key:
            return {"screen": "unknown", "notes": "no ANTHROPIC_API_KEY"}

        client = anthropic.Anthropic(api_key=self._api_key)
        img_b64 = base64.b64encode(img_bytes).decode()

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": VISION_PROMPT},
                ],
            }],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown fences if any
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[AutoLoader] Vision returned non-JSON: {raw[:120]}")
            return {"screen": "unknown", "notes": raw[:120]}
