"""
Emergent preference tracking — the agent develops likes and dislikes from experience.

Nothing is pre-configured. Preferences emerge from cumulative reward signals:
- High reward from gathering herbs → preference for gathering grows
- Dying near bandits → aversion to bandit camps grows
- Finding loot in caves → preference for cave exploration grows

Preferences influence strategy via the LLM prompt context ("I tend to enjoy X",
"I've learned to avoid Y") and via direct weighting of goal priorities.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Preference:
    """A single preference dimension. Affinity ranges from -1.0 (aversion) to +1.0 (desire)."""
    category: str          # e.g. "activity", "location", "item_type", "npc_type"
    name: str              # e.g. "exploring_caves", "gathering_herbs", "bandit_camp"
    affinity: float = 0.0  # -1.0 aversion … 0.0 neutral … +1.0 desire
    sample_count: int = 0  # how many experiences shaped this preference
    last_updated_ts: float = field(default_factory=time.time)


class PreferenceTracker:
    # How much a single reward signal shifts affinity (learning rate)
    LEARNING_RATE = 0.1
    # Preferences with fewer than this many samples are considered "uncertain"
    CONFIDENCE_THRESHOLD = 5

    def __init__(self, path: str = "./data/preferences.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._prefs: dict[str, Preference] = self._load()

    def _load(self) -> dict[str, Preference]:
        if self._path.exists():
            data = json.loads(self._path.read_text())
            return {k: Preference(**v) for k, v in data.items()}
        return {}

    def _save(self) -> None:
        self._path.write_text(json.dumps(
            {k: {"category": v.category, "name": v.name, "affinity": round(v.affinity, 4),
                 "sample_count": v.sample_count, "last_updated_ts": v.last_updated_ts}
             for k, v in self._prefs.items()},
            indent=2,
        ))

    def _key(self, category: str, name: str) -> str:
        return f"{category}:{name}"

    def update(self, category: str, name: str, reward: float) -> Preference:
        """
        Shift preference toward reward signal.
        reward > 0 → positive experience → affinity increases
        reward < 0 → negative experience → affinity decreases
        reward magnitude determines strength of signal (typically 0.0–1.0).
        """
        key = self._key(category, name)
        if key not in self._prefs:
            self._prefs[key] = Preference(category=category, name=name)

        pref = self._prefs[key]

        # Exponential moving average: blend toward the new signal
        # Early samples shift more (lower sample_count → larger effective rate)
        effective_rate = self.LEARNING_RATE * max(1.0, 3.0 / (1 + pref.sample_count))
        pref.affinity += effective_rate * (reward - pref.affinity)
        pref.affinity = max(-1.0, min(1.0, pref.affinity))
        pref.sample_count += 1
        pref.last_updated_ts = time.time()

        self._save()
        return pref

    def get(self, category: str, name: str) -> Preference | None:
        return self._prefs.get(self._key(category, name))

    def top_preferences(self, n: int = 5, category: str | None = None) -> list[Preference]:
        """Strongest positive preferences (things the agent enjoys)."""
        prefs = [p for p in self._prefs.values()
                 if (category is None or p.category == category)
                 and p.sample_count >= self.CONFIDENCE_THRESHOLD]
        prefs.sort(key=lambda p: p.affinity, reverse=True)
        return prefs[:n]

    def top_aversions(self, n: int = 5, category: str | None = None) -> list[Preference]:
        """Strongest negative preferences (things the agent avoids)."""
        prefs = [p for p in self._prefs.values()
                 if (category is None or p.category == category)
                 and p.sample_count >= self.CONFIDENCE_THRESHOLD]
        prefs.sort(key=lambda p: p.affinity)
        return prefs[:n]

    def describe_personality(self, n: int = 3) -> str:
        """Natural language summary of preferences for LLM context."""
        likes = self.top_preferences(n)
        dislikes = self.top_aversions(n)
        parts: list[str] = []
        if likes:
            like_strs = [f"{p.name} ({p.affinity:+.2f})" for p in likes if p.affinity > 0.1]
            if like_strs:
                parts.append(f"I tend to enjoy: {', '.join(like_strs)}")
        if dislikes:
            dislike_strs = [f"{p.name} ({p.affinity:+.2f})" for p in dislikes if p.affinity < -0.1]
            if dislike_strs:
                parts.append(f"I've learned to avoid: {', '.join(dislike_strs)}")
        return ". ".join(parts) if parts else "I haven't developed strong preferences yet."
