"""
Tracks where the agent has been and how familiar each location feels.
Familiarity decays toward 0.5 over time (nothing is completely forgotten, nothing stays certain).
"""
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class LocationMemory:
    scene: str
    familiarity: float = 0.0   # 0.0 = unknown, 1.0 = very familiar
    visit_count: int = 0
    last_visit_ts: float = field(default_factory=time.time)
    notes: list[str] = field(default_factory=list)


class MentalMap:
    DECAY_RATE = 0.01          # familiarity units lost per hour away
    FAMILIARITY_PER_VISIT = 0.15

    def __init__(self, map_path: str) -> None:
        self._path = Path(map_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._locations: dict[str, LocationMemory] = self._load()

    def _load(self) -> dict[str, LocationMemory]:
        if self._path.exists():
            data = json.loads(self._path.read_text())
            return {k: LocationMemory(**v) for k, v in data.items()}
        return {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(
            {k: asdict(v) for k, v in self._locations.items()}, indent=2
        ))

    def visit(self, scene: str) -> LocationMemory:
        now = time.time()
        if scene not in self._locations:
            self._locations[scene] = LocationMemory(scene=scene)
        loc = self._locations[scene]
        # Apply decay for time away
        hours_away = (now - loc.last_visit_ts) / 3600
        decay = self.DECAY_RATE * hours_away
        loc.familiarity = max(0.0, loc.familiarity - decay)
        # Boost familiarity for the visit
        loc.familiarity = min(1.0, loc.familiarity + self.FAMILIARITY_PER_VISIT)
        loc.visit_count += 1
        loc.last_visit_ts = now
        self._save()
        return loc

    def get(self, scene: str) -> LocationMemory | None:
        return self._locations.get(scene)

    def add_note(self, scene: str, note: str) -> None:
        if scene not in self._locations:
            self._locations[scene] = LocationMemory(scene=scene)
        self._locations[scene].notes.append(note)
        self._save()

    def most_familiar(self, n: int = 5) -> list[LocationMemory]:
        locs = sorted(self._locations.values(), key=lambda l: l.familiarity, reverse=True)
        return locs[:n]
