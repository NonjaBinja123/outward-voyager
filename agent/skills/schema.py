from dataclasses import dataclass, field
from typing import Any


@dataclass
class Skill:
    id: int | None
    name: str
    action_type: str          # e.g. "move", "interact", "use_item", "use_skill", "wait"
    parameters: dict[str, Any]
    preconditions: dict[str, Any]  # game state conditions required before executing
    tags: list[str]
    success_rate: float = 1.0
    times_used: int = 0
    times_succeeded: int = 0
    description: str = ""

    def record_outcome(self, success: bool) -> None:
        self.times_used += 1
        if success:
            self.times_succeeded += 1
        if self.times_used > 0:
            self.success_rate = self.times_succeeded / self.times_used
