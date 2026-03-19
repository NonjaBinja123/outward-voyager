"""
KeybindingLearner — discovers what keyboard shortcuts do what in Outward by:

  1. Hardcoded defaults (Outward's factory key layout) — always available.
  2. Rewired config file (~AppData/LocalLow/Nine Dots Studio/...) — reads the
     player's actual remapped keys if they exist.
  3. Vision discovery — takes a screenshot and asks Claude Haiku to read any
     on-screen key hints (HUD prompts, pause menu, tooltip overlays).

The learner persists everything it discovers to data/keybindings.json so each
source's confidence is remembered across sessions. get_key(action) always returns
the highest-confidence known binding for that action.

Integration: orchestrator calls get_key() before any press_key command, and
calls discover_from_screenshot() after opening menus or on a slow background
timer to opportunistically learn from whatever is visible.
"""
import asyncio
import base64
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from enum import IntEnum
from io import BytesIO
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Confidence ordering ───────────────────────────────────────────────────────

class Confidence(IntEnum):
    DEFAULT  = 0   # hardcoded fallback, may be wrong if remapped
    VISION   = 1   # seen on screen (pretty reliable)
    REWIRED  = 2   # read from Outward's Rewired config file (authoritative)
    OBSERVED = 3   # confirmed by seeing the menu actually open after pressing


# ── Outward default key layout ────────────────────────────────────────────────
# These are Outward's out-of-the-box keybindings.
# Keys are expressed as string names matching Windows VK names or simple chars.
# The C# mod's press_key command accepts: tab, escape, space, enter, and a-z.

_OUTWARD_DEFAULTS: dict[str, str] = {
    # Menus
    "inventory":    "tab",
    "bag":          "tab",
    "equipment":    "tab",
    "skills":       "k",
    "map":          "m",
    "quest":        "j",
    "journal":      "j",
    "pause":        "escape",
    # World interaction
    "interact":     "f",
    "dodge":        "space",
    "sprint":       "c",    # hold
    "attack":       "mouse1",
    "block":        "mouse2",
    # Quick slots
    "quick1":       "1",
    "quick2":       "2",
    "quick3":       "3",
    "quick4":       "4",
    # Camera / misc
    "camera_reset": "r",
    "crouch":       "g",
}

# Potential Rewired config paths for Outward on Windows
_REWIRED_SEARCH_PATHS: list[str] = [
    r"~\AppData\LocalLow\Nine Dots Studio\Outward Definitive Edition\input_manager_data.json",
    r"~\AppData\LocalLow\Nine Dots Studio\Outward\input_manager_data.json",
    r"~\AppData\Roaming\Nine Dots Studio\Outward Definitive Edition\input_manager_data.json",
]

_VISION_PROMPT = """\
You are watching a screenshot of "Outward Definitive Edition."

Look carefully for any keyboard shortcut hints, keybinding prompts, or button labels visible in the UI.
Examples: "[E] Interact", "[Tab] Inventory", "Press [K] for Skills", "F - Pick Up"

Respond with ONLY valid JSON (no markdown fences):
{
  "bindings": [
    {"action": "interact", "key": "e"},
    {"action": "inventory", "key": "tab"}
  ],
  "notes": "one sentence describing what UI is visible"
}

If no key hints are visible, return: {"bindings": [], "notes": "no key hints visible"}
Action names should be short lowercase words (interact, inventory, skills, pickup, dodge, etc).
Key names should be lowercase (tab, e, f, escape, space, etc).
"""


@dataclass
class Binding:
    action: str
    key: str
    confidence: int = Confidence.DEFAULT
    source: str = "default"


class KeybindingLearner:
    def __init__(self, data_dir: str = "./data") -> None:
        self._path = Path(data_dir) / "keybindings.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._bindings: dict[str, Binding] = {}
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")

        # Load defaults first (lowest confidence)
        for action, key in _OUTWARD_DEFAULTS.items():
            self._bindings[action] = Binding(action=action, key=key,
                                             confidence=Confidence.DEFAULT,
                                             source="default")

        # Load persisted overrides (may have higher confidence)
        self._load_from_file()

        # Try to read Rewired config (authoritative)
        self._load_from_rewired()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_key(self, action: str) -> str | None:
        """Return the best known key for this action, or None if unknown."""
        b = self._bindings.get(action.lower())
        return b.key if b else None

    def get_key_with_fallback(self, action: str, fallback: str) -> str:
        return self.get_key(action) or fallback

    def record_observation(self, action: str, key: str) -> None:
        """Record a confirmed observation (e.g. pressed Tab and inventory opened)."""
        self._set(action, key, Confidence.OBSERVED, "observed")

    def as_context_string(self) -> str:
        """Format current known bindings for inclusion in LLM strategy prompt."""
        lines = []
        for b in sorted(self._bindings.values(), key=lambda b: b.action):
            lines.append(f"  {b.action}: [{b.key.upper()}]")
        return "\n".join(lines)

    async def discover_from_screenshot(self) -> list[Binding]:
        """
        Take a screenshot and ask Claude Haiku what key hints are visible.
        Returns list of newly discovered (or confirmed) bindings.
        Updates internal state and persists if anything new is found.
        """
        if not self._api_key:
            logger.debug("[Keybindings] No ANTHROPIC_API_KEY — vision discovery skipped")
            return []

        try:
            img_bytes = await asyncio.to_thread(self._capture_screen)
            if not img_bytes:
                return []
            result = await asyncio.to_thread(self._ask_vision, img_bytes)
            discovered = []
            changed = False
            for entry in result.get("bindings", []):
                action = entry.get("action", "").lower().strip()
                key = entry.get("key", "").lower().strip()
                if not action or not key:
                    continue
                existing = self._bindings.get(action)
                if existing is None or existing.confidence < Confidence.VISION:
                    self._set(action, key, Confidence.VISION, "vision")
                    discovered.append(self._bindings[action])
                    changed = True
                    logger.info(f"[Keybindings] Vision learned: {action} → [{key}]")
            if changed:
                self._save()
            notes = result.get("notes", "")
            if notes:
                logger.debug(f"[Keybindings] Vision notes: {notes}")
            return discovered
        except Exception as e:
            logger.warning(f"[Keybindings] discover_from_screenshot failed: {e}")
            return []

    # ── Internals ─────────────────────────────────────────────────────────────

    def _set(self, action: str, key: str, confidence: int, source: str) -> None:
        existing = self._bindings.get(action)
        if existing is None or existing.confidence <= confidence:
            self._bindings[action] = Binding(action=action, key=key,
                                             confidence=confidence, source=source)

    def _load_from_file(self) -> None:
        if not self._path.exists():
            return
        try:
            data: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
            for action, entry in data.items():
                b = Binding(**entry)
                # Only load if confidence beats current (defaults are 0)
                if b.confidence > self._bindings.get(action, Binding("", "", -1)).confidence:
                    self._bindings[action] = b
            logger.info(f"[Keybindings] Loaded {len(data)} bindings from {self._path}")
        except Exception as e:
            logger.warning(f"[Keybindings] Failed to load {self._path}: {e}")

    def _save(self) -> None:
        try:
            data = {action: asdict(b) for action, b in self._bindings.items()}
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[Keybindings] Save failed: {e}")

    def _load_from_rewired(self) -> None:
        """Try to read Outward's Rewired config for the player's actual keybindings."""
        for raw_path in _REWIRED_SEARCH_PATHS:
            path = Path(raw_path.replace("~", str(Path.home())))
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                count = self._parse_rewired(data)
                if count > 0:
                    logger.info(f"[Keybindings] Read {count} bindings from Rewired config: {path}")
                    self._save()
                return
            except Exception as e:
                logger.debug(f"[Keybindings] Rewired parse failed ({path}): {e}")

    def _parse_rewired(self, data: Any) -> int:
        """
        Parse Rewired's input_manager_data.json format.
        Rewired stores action→key mappings in a nested structure. We extract
        any keyboard element maps (keyboardMaps) and record them.
        Returns count of bindings found.
        """
        count = 0
        # Rewired format varies by version; try to walk common structures
        if not isinstance(data, dict):
            return 0

        # Look for keyboard maps in common Rewired JSON structures
        keyboard_maps = self._find_in_dict(data, "keyboardMaps") or \
                        self._find_in_dict(data, "keyboardMap") or []

        for km in (keyboard_maps if isinstance(keyboard_maps, list) else [keyboard_maps]):
            for action_map in (km.get("maps") or km.get("actionElementMaps") or []):
                action_name = (action_map.get("actionDescriptiveName") or
                               action_map.get("actionId") or "")
                element_id = action_map.get("elementIdentifierId", -1)
                # Rewired element IDs for keyboard keys correspond to KeyCode values
                key_str = self._rewired_element_to_key(element_id)
                if action_name and key_str:
                    action_clean = action_name.lower().replace(" ", "_")
                    self._set(action_clean, key_str, Confidence.REWIRED, "rewired")
                    count += 1
        return count

    def _find_in_dict(self, data: dict, key: str) -> Any:
        if key in data:
            return data[key]
        for v in data.values():
            if isinstance(v, dict):
                result = self._find_in_dict(v, key)
                if result is not None:
                    return result
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        result = self._find_in_dict(item, key)
                        if result is not None:
                            return result
        return None

    def _rewired_element_to_key(self, element_id: int) -> str | None:
        """
        Map Rewired keyboard element IDs to key name strings.
        Rewired uses Unity KeyCode values for keyboard elements.
        Reference: https://docs.unity3d.com/ScriptReference/KeyCode.html
        """
        _MAP: dict[int, str] = {
            9: "tab", 13: "enter", 27: "escape", 32: "space",
            48: "0", 49: "1", 50: "2", 51: "3", 52: "4",
            53: "5", 54: "6", 55: "7", 56: "8", 57: "9",
            97: "a", 98: "b", 99: "c", 100: "d", 101: "e",
            102: "f", 103: "g", 104: "h", 105: "i", 106: "j",
            107: "k", 108: "l", 109: "m", 110: "n", 111: "o",
            112: "p", 113: "q", 114: "r", 115: "s", 116: "t",
            117: "u", 118: "v", 119: "w", 120: "x", 121: "y",
            122: "z",
        }
        return _MAP.get(element_id)

    def _capture_screen(self) -> bytes | None:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            img.thumbnail((1280, 720))
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except ImportError:
            logger.warning("[Keybindings] Pillow not installed — vision disabled")
            return None
        except Exception as e:
            logger.warning(f"[Keybindings] Screenshot failed: {e}")
            return None

    def _ask_vision(self, img_bytes: bytes) -> dict:
        import anthropic
        client = anthropic.Anthropic(api_key=self._api_key)
        img_b64 = base64.b64encode(img_bytes).decode()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                    },
                    {"type": "text", "text": _VISION_PROMPT},
                ],
            }],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"[Keybindings] Vision non-JSON response: {raw[:100]}")
            return {"bindings": [], "notes": raw[:100]}
