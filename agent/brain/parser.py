"""
Parser — extracts and validates the action plan from raw LLM output.

Responsible ONLY for: turning a raw string into a structured dict (or None).
No LLM calls. No game knowledge. Purely text → structured data.

Test in isolation:
    from brain.parser import parse
    result = parse('{"thinking": "...", "actions": [{"action": "wait", "params": {"seconds": 2}}]}')
"""
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Required keys in a valid response
_REQUIRED_KEYS = {"actions"}


def parse(raw: str) -> dict[str, Any] | None:
    """
    Parse raw LLM output into a validated action plan dict.

    Handles:
    - <think>...</think> reasoning blocks (qwen3)
    - ```json ... ``` markdown fences
    - JSON embedded in surrounding prose
    - Partial/truncated responses

    Returns None if no valid plan can be extracted.
    """
    if not raw:
        return None

    text = _strip_thinking(raw)
    text = _strip_fences(text)

    data = _try_json(text)
    if data is None:
        data = _scan_for_json(text)
    if data is None:
        logger.warning(f"[Parser] Could not parse LLM response: {text[:200]}")
        return None

    return _validate(data)


# ── Internals ─────────────────────────────────────────────────────────────────

def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> CoT blocks produced by qwen3 and similar models."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _strip_fences(text: str) -> str:
    """Remove ```json ... ``` or ``` ... ``` markdown fences."""
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _try_json(text: str) -> dict | None:
    """Attempt a direct json.loads on the full text."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _scan_for_json(text: str) -> dict | None:
    """
    Scan character-by-character for a balanced JSON object.
    Handles Claude/qwen prepending prose before the JSON.
    """
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        for j, c in enumerate(text[i:], i):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = _try_json(text[i:j + 1])
                    if candidate is not None:
                        return candidate
                    break
    return None


def _validate(data: dict) -> dict[str, Any] | None:
    """Check that the parsed dict has required keys with correct types."""
    actions = data.get("actions")
    if not isinstance(actions, list):
        logger.warning(f"[Parser] No 'actions' list in response: {data}")
        return None
    logger.debug(f"[Parser] Plan: {data.get('thinking', '')[:80]}")
    return data
