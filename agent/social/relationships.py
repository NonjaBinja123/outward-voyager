"""
RelationshipEngine — builds a persistent model of each player Voyager has met.

For each player, tracks:
  - Disposition (-1.0 hostile → 0.0 neutral → +1.0 friendly)
  - Trust (0.0–1.0): how much the agent defers to this player's requests
  - Inferred traits: ["helpful", "aggressive", "curious", "commander", ...]
  - Interaction history summary

Disposition and trust evolve from sentiment signals emitted by SocialMemoryManager
interactions. The RelationshipEngine is the "feelings" layer; SocialMemoryManager
is the "facts" layer.

Persistence: data/relationships.json — one entry per player, updated on every
new interaction.
"""
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "./data/relationships.json"

# How quickly trust decays per day of no interaction
_TRUST_DECAY_PER_DAY = 0.05
# How quickly disposition drifts back toward neutral per day
_DISPOSITION_DRIFT_PER_DAY = 0.03


@dataclass
class PlayerRelationship:
    player: str
    disposition: float = 0.0        # -1.0 hostile → +1.0 friendly
    trust: float = 0.1              # 0.0–1.0: how much we weight their requests
    interaction_count: int = 0
    last_seen_ts: float = field(default_factory=time.time)
    last_scene: str = ""
    inferred_traits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def is_friendly(self) -> bool:
        return self.disposition > 0.25

    @property
    def is_hostile(self) -> bool:
        return self.disposition < -0.25

    @property
    def is_trusted(self) -> bool:
        return self.trust > 0.5

    @property
    def disposition_label(self) -> str:
        if self.disposition > 0.6:
            return "close friend"
        if self.disposition > 0.25:
            return "friendly"
        if self.disposition < -0.6:
            return "enemy"
        if self.disposition < -0.25:
            return "hostile"
        return "neutral"

    def short_summary(self) -> str:
        traits = ", ".join(self.inferred_traits[:3]) if self.inferred_traits else "unknown"
        return (
            f"{self.player}: {self.disposition_label} "
            f"(disposition={self.disposition:+.2f}, trust={self.trust:.2f}), "
            f"traits=[{traits}], seen {self.interaction_count}×"
        )


class RelationshipEngine:
    def __init__(self, data_path: str = _DEFAULT_PATH) -> None:
        self._path = Path(data_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._players: dict[str, PlayerRelationship] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, player: str) -> PlayerRelationship:
        """Get or create a relationship record for this player."""
        if player not in self._players:
            self._players[player] = PlayerRelationship(player=player)
        return self._players[player]

    def has_met(self, player: str) -> bool:
        return player in self._players

    def update_from_interaction(
        self,
        player: str,
        sentiment: float,
        scene: str = "",
        trait_hint: str | None = None,
    ) -> PlayerRelationship:
        """
        Update a player's relationship after an interaction.

        sentiment: -1.0 to +1.0 — how positive/negative this exchange was
        trait_hint: optional string like "helpful" or "aggressive" to record
        """
        rel = self.get(player)

        # Apply time-based decay since last seen
        self._apply_decay(rel)

        # Update disposition: smooth blend toward sentiment
        # Each interaction moves disposition by up to ±0.15, weighted by trust
        shift = sentiment * 0.10 * (1.0 + rel.trust)
        rel.disposition = max(-1.0, min(1.0, rel.disposition + shift))

        # Trust grows slowly with positive interactions, falls with negative
        if sentiment > 0:
            rel.trust = min(1.0, rel.trust + 0.03)
        elif sentiment < -0.3:
            rel.trust = max(0.0, rel.trust - 0.05)

        rel.interaction_count += 1
        rel.last_seen_ts = time.time()
        if scene:
            rel.last_scene = scene

        if trait_hint and trait_hint not in rel.inferred_traits:
            rel.inferred_traits.append(trait_hint)
            # Keep trait list bounded
            if len(rel.inferred_traits) > 10:
                rel.inferred_traits = rel.inferred_traits[-10:]

        self._save()
        logger.debug(
            f"[Relationships] {player}: disposition={rel.disposition:+.2f} "
            f"trust={rel.trust:.2f} after sentiment={sentiment:+.2f}"
        )
        return rel

    def add_note(self, player: str, note: str) -> None:
        rel = self.get(player)
        rel.notes.append(note)
        if len(rel.notes) > 20:
            rel.notes = rel.notes[-20:]
        self._save()

    def all_players(self) -> list[PlayerRelationship]:
        return sorted(self._players.values(), key=lambda r: r.last_seen_ts, reverse=True)

    def context_block(self) -> str:
        """Format known players for LLM strategy context."""
        if not self._players:
            return "No players met yet."
        lines = [rel.short_summary() for rel in self.all_players()[:8]]
        return "\n".join(f"  {l}" for l in lines)

    def infer_sentiment(self, message: str) -> float:
        """
        Heuristic sentiment inference from message text.
        A proper implementation would call an LLM; this is a fast rule-based fallback
        good enough for real-time processing.
        """
        msg = message.lower()

        positive = ["thank", "thanks", "great", "awesome", "nice", "good job",
                    "love", "like", "help", "please", "appreciate", "well done",
                    "cool", "amazing", "excellent", "perfect", "wonderful"]
        negative = ["kill", "die", "stop", "shut up", "hate", "terrible",
                    "awful", "useless", "stupid", "idiot", "worst", "bad",
                    "annoying", "leave me alone", "go away"]
        commanding = ["do this", "go to", "pick up", "attack", "run", "use",
                      "take", "give me", "get me", "find"]

        score = 0.0
        for word in positive:
            if word in msg:
                score += 0.2
        for word in negative:
            if word in msg:
                score -= 0.3
        for word in commanding:
            if word in msg:
                score -= 0.05  # Neutral slight negative — commanding tone

        return max(-1.0, min(1.0, score))

    def infer_trait(self, message: str) -> str | None:
        """Infer a personality trait from a single message."""
        msg = message.lower()
        if any(w in msg for w in ["help", "can you", "please", "would you"]):
            return "polite"
        if any(w in msg for w in ["kill", "attack", "fight", "destroy"]):
            return "aggressive"
        if any(w in msg for w in ["what", "how", "why", "tell me", "explain"]):
            return "curious"
        if any(w in msg for w in ["go", "do", "take", "get", "bring", "run"]):
            return "commanding"
        if any(w in msg for w in ["hi", "hello", "hey", "greet"]):
            return "social"
        return None

    # ── Internals ─────────────────────────────────────────────────────────────

    def _apply_decay(self, rel: PlayerRelationship) -> None:
        """Slowly drift disposition toward neutral and trust downward over time."""
        days_absent = (time.time() - rel.last_seen_ts) / 86400
        if days_absent < 0.1:
            return

        # Disposition drifts toward 0
        drift = _DISPOSITION_DRIFT_PER_DAY * days_absent
        if rel.disposition > 0:
            rel.disposition = max(0.0, rel.disposition - drift)
        elif rel.disposition < 0:
            rel.disposition = min(0.0, rel.disposition + drift)

        # Trust decays
        rel.trust = max(0.05, rel.trust - _TRUST_DECAY_PER_DAY * days_absent)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for player, entry in data.items():
                self._players[player] = PlayerRelationship(**entry)
            logger.info(f"[Relationships] Loaded {len(self._players)} player relationships")
        except Exception as e:
            logger.warning(f"[Relationships] Load failed: {e}")

    def _save(self) -> None:
        try:
            data = {p: asdict(r) for p, r in self._players.items()}
            self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[Relationships] Save failed: {e}")
