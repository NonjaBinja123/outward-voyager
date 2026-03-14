"""
Skill composition: turn LLM strategy output into a concrete skill sequence.
"""
from typing import Any

from .database import SkillDatabase
from .schema import Skill


class SkillComposer:
    def __init__(self, db: SkillDatabase) -> None:
        self._db = db

    def compose(self, intent: str, context: dict[str, Any]) -> list[Skill]:
        """
        Given a high-level intent string (from LLM) and current game context,
        return an ordered list of skills to execute.
        """
        # Simple tag-based lookup for now; will become LLM-assisted later
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
