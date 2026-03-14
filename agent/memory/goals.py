import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Goal:
    id: str
    description: str
    priority: int = 5          # 1 (low) – 10 (high)
    completed: bool = False
    tags: list[str] = field(default_factory=list)


class GoalSystem:
    def __init__(self, session_path: str, long_term_path: str) -> None:
        self._session_path = Path(session_path)
        self._long_term_path = Path(long_term_path)
        self._session_path.parent.mkdir(parents=True, exist_ok=True)
        self._long_term_path.parent.mkdir(parents=True, exist_ok=True)
        self.session: list[Goal] = self._load(self._session_path)
        self.long_term: list[Goal] = self._load(self._long_term_path)

    def _load(self, path: Path) -> list[Goal]:
        if path.exists():
            data = json.loads(path.read_text())
            return [Goal(**g) for g in data]
        return []

    def _save(self) -> None:
        self._session_path.write_text(json.dumps([asdict(g) for g in self.session], indent=2))
        self._long_term_path.write_text(json.dumps([asdict(g) for g in self.long_term], indent=2))

    def add_session_goal(self, goal: Goal) -> None:
        self.session.append(goal)
        self._save()

    def add_long_term_goal(self, goal: Goal) -> None:
        self.long_term.append(goal)
        self._save()

    def complete(self, goal_id: str) -> None:
        for g in self.session + self.long_term:
            if g.id == goal_id:
                g.completed = True
        self._save()

    def active_session_goals(self) -> list[Goal]:
        return [g for g in self.session if not g.completed]

    def active_long_term_goals(self) -> list[Goal]:
        return [g for g in self.long_term if not g.completed]

    def top_priority(self) -> Goal | None:
        active = self.active_session_goals() or self.active_long_term_goals()
        return max(active, key=lambda g: g.priority) if active else None

    def reset_session(self) -> None:
        self.session = []
        self._save()
