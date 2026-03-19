"""
Skill composition: turn LLM strategy output into a concrete skill sequence.
"""
from typing import Any

from .database import SkillDatabase
from .schema import Skill


class SkillComposer:
    def __init__(self, db: SkillDatabase, game_id: str = "") -> None:
        self._db = db
        self._game_id = game_id  # current connected game; filters skill scope

    def set_game_id(self, game_id: str) -> None:
        """Update active game when a new adapter connects."""
        self._game_id = game_id

    def compose(self, intent: str, context: dict[str, Any]) -> list[Skill]:
        """
        Given a high-level intent string (from LLM) and current game context,
        return an ordered list of skills to execute.

        Only returns skills valid for the current game:
          - cross_game skills always qualify
          - game_specific skills only if source_game_id matches self._game_id
          - archived skills are never returned
        """
        # Phase 9: filter by game scope first, then tag-match within that set
        if self._game_id:
            all_valid = self._db.get_for_game(self._game_id)
        else:
            all_valid = self._db.get_by_tag(intent)

        # Further filter by tag match
        candidates = [s for s in all_valid if intent in s.tags] if self._game_id else all_valid
        if not candidates:
            # Fallback: ignore game scope and just tag-match (for pre-portability instances)
            candidates = self._db.get_by_tag(intent)
        if not candidates:
            return []
        # Sort by success rate descending
        candidates.sort(key=lambda s: s.success_rate, reverse=True)
        return candidates[:3]

    def prune_failing(self, threshold: float = 0.3) -> list[str]:
        """Delete skills below threshold and return their names."""
        failing = self._db.get_failing(threshold)
        pruned = []
        for skill in failing:
            self._db.delete(skill.name)
            pruned.append(skill.name)
        return pruned
