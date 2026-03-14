"""
Combat learning — tracks outcomes of combat encounters per enemy type.

The agent records every combat encounter: which enemies were present,
whether it survived, HP lost, and the combat duration proxy. Over time,
patterns emerge: certain enemy types cost more HP, certain locations are
dangerous, and the agent becomes more cautious or avoidant accordingly.

Data is persisted to data/combat_log.json and feeds into:
  - RewardEngine (combat outcome signals)
  - Strategy loop (enemy avoidance / engagement decisions)
  - Preference tracker (dangerous enemies → aversion)
"""
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CombatRecord:
    """A single recorded combat encounter."""
    timestamp: float
    scene: str
    enemies: list[str]           # enemy names nearby when combat started
    survived: bool
    hp_before: float
    hp_after: float
    max_hp: float
    duration_ticks: int          # how many state ticks combat lasted

    @property
    def hp_loss_pct(self) -> float:
        if self.max_hp <= 0:
            return 0.0
        return max(0.0, (self.hp_before - self.hp_after) / self.max_hp)

    @property
    def threat_score(self) -> float:
        """0 = trivial, 1 = lethal. Combines survival and HP loss."""
        survival_penalty = 0.8 if not self.survived else 0.0
        return min(1.0, self.hp_loss_pct + survival_penalty)


@dataclass
class EnemyProfile:
    """Aggregated threat data for a specific enemy type."""
    name: str
    encounter_count: int = 0
    survival_count: int = 0
    total_hp_loss_pct: float = 0.0
    last_seen_ts: float = field(default_factory=time.time)

    @property
    def survival_rate(self) -> float:
        if self.encounter_count == 0:
            return 1.0
        return self.survival_count / self.encounter_count

    @property
    def avg_hp_loss_pct(self) -> float:
        if self.encounter_count == 0:
            return 0.0
        return self.total_hp_loss_pct / self.encounter_count

    @property
    def threat_level(self) -> str:
        """Human-readable threat classification."""
        score = (1 - self.survival_rate) * 0.5 + self.avg_hp_loss_pct * 0.5
        if score < 0.1:
            return "trivial"
        if score < 0.3:
            return "low"
        if score < 0.5:
            return "moderate"
        if score < 0.7:
            return "high"
        return "lethal"


class CombatLearner:
    """Tracks combat encounters and builds enemy threat profiles."""

    def __init__(self, data_dir: str = "./data") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._data_dir / "combat_log.json"
        self._profiles: dict[str, EnemyProfile] = {}
        self._records: list[CombatRecord] = []
        self._load()

        # Track in-progress combat
        self._combat_start_state: dict[str, Any] | None = None
        self._combat_tick_count: int = 0

    def on_combat_enter(self, state: dict[str, Any]) -> None:
        """Called when combat state transitions to True."""
        self._combat_start_state = state
        self._combat_tick_count = 0
        enemies = [e.get("name", "unknown") for e in state.get("nearby_dead", [])]
        logger.info(f"[Combat] Entered combat. Nearby: {enemies}")

    def on_combat_tick(self) -> None:
        """Called each state update while in_combat is True."""
        self._combat_tick_count += 1

    def on_combat_exit(self, state: dict[str, Any], died: bool = False) -> None:
        """Called when combat ends (survived or died)."""
        if self._combat_start_state is None:
            return

        start = self._combat_start_state
        player_before = start.get("player", {})
        player_after = state.get("player", {})

        hp_before = float(player_before.get("health", 0))
        hp_after = float(player_after.get("health", 0))
        max_hp = float(player_after.get("max_health", 1))

        # Nearby objects at combat start as enemy proxies (corpse detection)
        enemies = [e.get("name", "unknown") for e in start.get("nearby_dead", [])]
        if not enemies:
            enemies = ["unknown_enemy"]

        record = CombatRecord(
            timestamp=time.time(),
            scene=state.get("scene", "unknown"),
            enemies=enemies,
            survived=not died,
            hp_before=hp_before,
            hp_after=hp_after,
            max_hp=max_hp,
            duration_ticks=self._combat_tick_count,
        )
        self._records.append(record)
        self._update_profiles(record)
        self._save()

        status = "died" if died else "survived"
        logger.info(
            f"[Combat] {status} | HP {hp_before:.0f}→{hp_after:.0f}/{max_hp:.0f} "
            f"({record.hp_loss_pct:.0%} loss) | {self._combat_tick_count} ticks"
        )
        self._combat_start_state = None
        self._combat_tick_count = 0

    def _update_profiles(self, record: CombatRecord) -> None:
        for enemy_name in record.enemies:
            if enemy_name not in self._profiles:
                self._profiles[enemy_name] = EnemyProfile(name=enemy_name)
            prof = self._profiles[enemy_name]
            prof.encounter_count += 1
            if record.survived:
                prof.survival_count += 1
            prof.total_hp_loss_pct += record.hp_loss_pct
            prof.last_seen_ts = record.timestamp

    def get_profile(self, enemy_name: str) -> EnemyProfile | None:
        return self._profiles.get(enemy_name)

    def most_dangerous(self, n: int = 5) -> list[EnemyProfile]:
        """Return the most threatening enemy types by threat score."""
        profiles = [p for p in self._profiles.values() if p.encounter_count >= 2]
        profiles.sort(key=lambda p: p.avg_hp_loss_pct + (1 - p.survival_rate), reverse=True)
        return profiles[:n]

    def describe_combat_experience(self) -> str:
        """Natural language summary for LLM context."""
        dangerous = self.most_dangerous(3)
        if not dangerous:
            return "I have no combat experience yet."
        parts = []
        for p in dangerous:
            parts.append(f"{p.name} ({p.threat_level} threat, {p.survival_rate:.0%} survival rate)")
        return "Dangerous enemies I've faced: " + "; ".join(parts)

    # ── Persistence ─────────────────────────────────────────────────────────

    def _save(self) -> None:
        data = {
            "profiles": {
                name: {
                    "name": p.name,
                    "encounter_count": p.encounter_count,
                    "survival_count": p.survival_count,
                    "total_hp_loss_pct": round(p.total_hp_loss_pct, 4),
                    "last_seen_ts": p.last_seen_ts,
                }
                for name, p in self._profiles.items()
            },
            "recent_records": [
                {
                    "timestamp": r.timestamp,
                    "scene": r.scene,
                    "enemies": r.enemies,
                    "survived": r.survived,
                    "hp_loss_pct": round(r.hp_loss_pct, 4),
                    "duration_ticks": r.duration_ticks,
                }
                for r in self._records[-100:]  # keep last 100
            ],
        }
        self._log_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load(self) -> None:
        if not self._log_path.exists():
            return
        try:
            data = json.loads(self._log_path.read_text(encoding="utf-8"))
            for name, p in data.get("profiles", {}).items():
                self._profiles[name] = EnemyProfile(**p)
        except Exception as e:
            logger.warning(f"[Combat] Failed to load combat log: {e}")
