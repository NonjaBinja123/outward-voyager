"""
Reward engine — processes game state changes into reward signals that drive
the agent's emergent behavior.

Reward sources:
  - Discovery: new scenes, objects, items → novelty score
  - Survival: health/stamina changes → positive for recovery, negative for damage
  - Social: player interaction → small positive signal for engagement
  - Combat: surviving a fight → positive; dying → strong negative
  - Exploration: visiting unfamiliar locations → novelty-weighted positive

The engine is called by the orchestrator after each state update. It:
  1. Compares current state to previous state
  2. Emits reward signals for detected changes
  3. Updates novelty tracker and preference tracker
  4. Returns a RewardFrame summarizing this tick's rewards
"""
import logging
from dataclasses import dataclass, field
from typing import Any

from reward.novelty import NoveltyTracker
from reward.preferences import PreferenceTracker

logger = logging.getLogger(__name__)


@dataclass
class RewardSignal:
    source: str       # e.g. "discovery", "survival", "combat", "social"
    category: str     # e.g. "scene", "item", "activity"
    name: str         # e.g. "CierzoTutorial", "Iron Sword"
    value: float      # -1.0 to +1.0
    reason: str = ""  # human-readable explanation


@dataclass
class RewardFrame:
    """All rewards generated in a single tick."""
    signals: list[RewardSignal] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(s.value for s in self.signals)

    @property
    def summary(self) -> str:
        if not self.signals:
            return ""
        top = max(self.signals, key=lambda s: abs(s.value))
        return f"{top.source}/{top.name}: {top.value:+.2f} ({top.reason})"


class RewardEngine:
    def __init__(self, data_dir: str = "./data") -> None:
        self._novelty = NoveltyTracker(f"{data_dir}/novelty.json")
        self._prefs = PreferenceTracker(f"{data_dir}/preferences.json")
        self._prev_state: dict[str, Any] = {}

    @property
    def novelty(self) -> NoveltyTracker:
        return self._novelty

    @property
    def preferences(self) -> PreferenceTracker:
        return self._prefs

    def process(self, state: dict[str, Any]) -> RewardFrame:
        """Compare state to previous, emit reward signals, update trackers."""
        frame = RewardFrame()

        if not self._prev_state:
            self._prev_state = state
            return frame

        prev = self._prev_state
        self._prev_state = state

        self._check_scene_change(prev, state, frame)
        self._check_health_change(prev, state, frame)
        self._check_combat_state(prev, state, frame)
        self._check_death(prev, state, frame)
        self._check_nearby_discoveries(state, frame)

        # Feed all signals into the preference tracker
        for sig in frame.signals:
            self._prefs.update(sig.category, sig.name, sig.value)

        if frame.signals:
            logger.info(f"[Reward] {len(frame.signals)} signals, total={frame.total:+.2f} | {frame.summary}")

        return frame

    def _check_scene_change(self, prev: dict, curr: dict, frame: RewardFrame) -> None:
        prev_scene = prev.get("scene", "")
        curr_scene = curr.get("scene", "")
        if curr_scene and curr_scene != prev_scene:
            novelty = self._novelty.encounter("scene", curr_scene)
            frame.signals.append(RewardSignal(
                source="discovery", category="scene", name=curr_scene,
                value=0.3 * novelty,
                reason=f"entered {'new' if novelty > 0.8 else 'familiar'} area",
            ))

    def _check_health_change(self, prev: dict, curr: dict, frame: RewardFrame) -> None:
        prev_hp = prev.get("player", {}).get("health", 0)
        curr_hp = curr.get("player", {}).get("health", 0)
        max_hp = curr.get("player", {}).get("max_health", 1)
        if max_hp <= 0:
            return

        delta_pct = (curr_hp - prev_hp) / max_hp
        if abs(delta_pct) < 0.01:
            return

        if delta_pct > 0:
            frame.signals.append(RewardSignal(
                source="survival", category="activity", name="healing",
                value=min(0.3, delta_pct),
                reason=f"healed {delta_pct:.0%}",
            ))
        else:
            frame.signals.append(RewardSignal(
                source="survival", category="activity", name="taking_damage",
                value=max(-0.5, delta_pct),
                reason=f"took {abs(delta_pct):.0%} damage",
            ))

    def _check_combat_state(self, prev: dict, curr: dict, frame: RewardFrame) -> None:
        was_in_combat = prev.get("player", {}).get("in_combat", False)
        in_combat = curr.get("player", {}).get("in_combat", False)

        if was_in_combat and not in_combat:
            # Survived combat
            curr_hp_pct = curr.get("player", {}).get("health", 0) / max(
                1, curr.get("player", {}).get("max_health", 1))
            frame.signals.append(RewardSignal(
                source="combat", category="activity", name="survived_combat",
                value=0.2 + 0.3 * curr_hp_pct,  # more reward for surviving healthy
                reason=f"survived fight at {curr_hp_pct:.0%} HP",
            ))

    def _check_death(self, prev: dict, curr: dict, frame: RewardFrame) -> None:
        was_dead = prev.get("player", {}).get("is_dead", False)
        is_dead = curr.get("player", {}).get("is_dead", False)
        if is_dead and not was_dead:
            scene = curr.get("scene", "unknown")
            frame.signals.append(RewardSignal(
                source="combat", category="location", name=scene,
                value=-0.8,
                reason=f"died in {scene}",
            ))

    def _check_nearby_discoveries(self, curr: dict, frame: RewardFrame) -> None:
        """Score novelty for any dead bodies or notable objects detected nearby."""
        nearby_dead = curr.get("nearby_dead", [])
        for entry in nearby_dead:
            name = entry.get("name", "unknown")
            novelty = self._novelty.encounter("object", name)
            if novelty > 0.5:
                frame.signals.append(RewardSignal(
                    source="discovery", category="object", name=name,
                    value=0.1 * novelty,
                    reason=f"noticed {name}",
                ))
