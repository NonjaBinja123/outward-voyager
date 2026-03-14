"""
Novelty decay — new experiences give high reward, repeated ones decay exponentially.

Each "experience" is a (category, key) pair, e.g. ("scene", "CierzoTutorial") or
("item", "Iron Sword"). The first encounter scores 1.0; subsequent encounters decay
toward a floor. Time also partially restores novelty (you can be re-surprised by
something you haven't seen in a while).
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class NoveltyRecord:
    encounter_count: int = 0
    first_seen_ts: float = 0.0
    last_seen_ts: float = 0.0


class NoveltyTracker:
    # How fast novelty drops per repeated encounter (0–1, lower = faster decay)
    DECAY_FACTOR = 0.6
    # Minimum novelty — even the most familiar thing has some residual interest
    NOVELTY_FLOOR = 0.05
    # Novelty recovery rate per hour since last seen
    TIME_RECOVERY_PER_HOUR = 0.02

    def __init__(self, path: str = "./data/novelty.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, NoveltyRecord] = self._load()

    def _load(self) -> dict[str, NoveltyRecord]:
        if self._path.exists():
            data = json.loads(self._path.read_text())
            return {k: NoveltyRecord(**v) for k, v in data.items()}
        return {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(
            {k: {"encounter_count": v.encounter_count,
                 "first_seen_ts": v.first_seen_ts,
                 "last_seen_ts": v.last_seen_ts}
             for k, v in self._records.items()},
            indent=2,
        ))

    def _key(self, category: str, name: str) -> str:
        return f"{category}:{name}"

    def encounter(self, category: str, name: str) -> float:
        """Record an encounter and return the novelty score (0.0–1.0)."""
        key = self._key(category, name)
        now = time.time()

        if key not in self._records:
            # First time seeing this — maximum novelty
            self._records[key] = NoveltyRecord(
                encounter_count=1, first_seen_ts=now, last_seen_ts=now,
            )
            self._save()
            return 1.0

        rec = self._records[key]

        # Time-based recovery: being away from something partially restores novelty
        hours_since = (now - rec.last_seen_ts) / 3600
        time_bonus = min(0.3, hours_since * self.TIME_RECOVERY_PER_HOUR)

        # Exponential decay based on encounter count
        base_novelty = self.DECAY_FACTOR ** rec.encounter_count

        # Combine: decayed novelty + time recovery, clamped to [floor, 1.0]
        novelty = max(self.NOVELTY_FLOOR, min(1.0, base_novelty + time_bonus))

        rec.encounter_count += 1
        rec.last_seen_ts = now
        self._save()
        return novelty

    def peek(self, category: str, name: str) -> float:
        """Check novelty score without recording an encounter."""
        key = self._key(category, name)
        if key not in self._records:
            return 1.0
        rec = self._records[key]
        hours_since = (time.time() - rec.last_seen_ts) / 3600
        time_bonus = min(0.3, hours_since * self.TIME_RECOVERY_PER_HOUR)
        base_novelty = self.DECAY_FACTOR ** rec.encounter_count
        return max(self.NOVELTY_FLOOR, min(1.0, base_novelty + time_bonus))

    def most_novel(self, category: str | None = None, n: int = 5) -> list[tuple[str, float]]:
        """Return the most novel experiences, optionally filtered by category."""
        items = []
        for key, rec in self._records.items():
            if category and not key.startswith(f"{category}:"):
                continue
            score = self.peek(*key.split(":", 1))
            items.append((key, score))
        items.sort(key=lambda x: x[1], reverse=True)
        return items[:n]
