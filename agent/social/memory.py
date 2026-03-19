"""
SocialMemoryManager — records every interaction the agent has with players and
lets the agent recall what was said, what the player seemed to want, and what
happened as a result.

Each interaction record captures:
  - who said it (player name)
  - what they said
  - what the agent did in response
  - the scene and timestamp
  - any outcome the agent noticed afterward (e.g. "player left", "player thanked me")

Records are stored as JSONL in data/social_memory.jsonl — simple, never corrupted,
easy to grep. The manager also maintains an in-memory index for fast lookup by
player name and a recent-first ordered list for context injection.
"""
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_LOG = "./data/social_memory.jsonl"


@dataclass
class Interaction:
    player: str            # In-game character name
    message: str           # What they said
    agent_response: str    # What Voyager said back ("" if ignored/not yet responded)
    scene: str             # Scene name where this happened
    timestamp: float = field(default_factory=time.time)
    outcome: str = ""      # Optional: what happened after ("player left", "helped them", etc.)
    sentiment: float = 0.0 # -1.0 hostile → 0.0 neutral → +1.0 friendly (inferred)
    tags: list[str] = field(default_factory=list)
    # Phase 9 — cross-game provenance
    game_id: str = ""              # e.g. "outward_definitive"
    game_display_name: str = ""    # e.g. "Outward Definitive Edition"


class SocialMemoryManager:
    def __init__(self, log_path: str = _DEFAULT_LOG) -> None:
        self._path = Path(log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[Interaction] = []
        self._by_player: dict[str, list[Interaction]] = {}
        self._load()

    # ── Recording ─────────────────────────────────────────────────────────────

    def record(self, interaction: Interaction) -> None:
        """Save an interaction to memory."""
        self._records.append(interaction)
        self._by_player.setdefault(interaction.player, []).append(interaction)
        self._append_to_disk(interaction)
        logger.debug(f"[Social] Recorded interaction with {interaction.player!r}")

    def record_message(
        self,
        player: str,
        message: str,
        scene: str,
        agent_response: str = "",
        sentiment: float = 0.0,
        tags: list[str] | None = None,
        game_id: str = "",
        game_display_name: str = "",
    ) -> Interaction:
        """Convenience wrapper — creates and records an Interaction."""
        ix = Interaction(
            player=player,
            message=message,
            agent_response=agent_response,
            scene=scene,
            sentiment=sentiment,
            tags=tags or [],
            game_id=game_id,
            game_display_name=game_display_name,
        )
        self.record(ix)
        return ix

    def update_response(self, interaction: Interaction, response: str) -> None:
        """Fill in the agent's response after it is generated. Rewrites last disk entry."""
        interaction.agent_response = response
        # Efficient: just append a correction record (reading the full log back is expensive)
        self._append_to_disk(interaction)

    def record_outcome(self, interaction: Interaction, outcome: str) -> None:
        """Record what happened after an interaction (optional enrichment)."""
        interaction.outcome = outcome
        self._append_to_disk(interaction)

    # ── Recall ────────────────────────────────────────────────────────────────

    def recent(self, n: int = 10) -> list[Interaction]:
        """Most recent interactions across all players."""
        return sorted(self._records, key=lambda r: r.timestamp, reverse=True)[:n]

    def for_player(self, player: str, n: int = 20) -> list[Interaction]:
        """All remembered interactions with a specific player, newest first."""
        records = self._by_player.get(player, [])
        return sorted(records, key=lambda r: r.timestamp, reverse=True)[:n]

    def known_players(self) -> list[str]:
        """All player names the agent has ever interacted with."""
        return list(self._by_player.keys())

    def has_met(self, player: str) -> bool:
        return player in self._by_player

    def first_met(self, player: str) -> float | None:
        records = self._by_player.get(player, [])
        return min(r.timestamp for r in records) if records else None

    def interaction_count(self, player: str) -> int:
        return len(self._by_player.get(player, []))

    def summary_for_player(self, player: str) -> str:
        """
        Returns a one-paragraph summary of this player suitable for injection
        into the LLM strategy context.
        """
        records = self.for_player(player, n=5)
        if not records:
            return f"{player}: never spoken to."

        count = self.interaction_count(player)
        first = self.first_met(player)
        avg_sentiment = sum(r.sentiment for r in self._by_player[player]) / count

        mood = "friendly" if avg_sentiment > 0.2 else "hostile" if avg_sentiment < -0.2 else "neutral"
        recent_msg = records[0].message[:80]
        return (
            f"{player}: met {count}× (first {_ago(first)}). "
            f"Generally {mood}. Last said: \"{recent_msg}\""
        )

    def context_block(self, current_scene: str | None = None, n: int = 5) -> str:
        """
        Format recent interactions as a text block for the strategy prompt.
        Prioritises interactions in the current scene.
        """
        if not self._records:
            return "No player interactions yet."

        recent = self.recent(n * 2)
        if current_scene:
            scene_records = [r for r in recent if r.scene == current_scene]
            other_records = [r for r in recent if r.scene != current_scene]
            ordered = (scene_records + other_records)[:n]
        else:
            ordered = recent[:n]

        lines = []
        for r in ordered:
            ago = _ago(r.timestamp)
            resp = f' → "{r.agent_response}"' if r.agent_response else ""
            lines.append(f'  [{ago}] {r.player}: "{r.message}"{resp}')
        return "\n".join(lines)

    # ── Internals ─────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        ix = Interaction(**data)
                        self._records.append(ix)
                        self._by_player.setdefault(ix.player, []).append(ix)
                    except Exception:
                        pass
            logger.info(f"[Social] Loaded {len(self._records)} interaction records")
        except Exception as e:
            logger.warning(f"[Social] Load failed: {e}")

    def _append_to_disk(self, ix: Interaction) -> None:
        try:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(ix)) + "\n")
        except Exception as e:
            logger.warning(f"[Social] Write failed: {e}")


def _ago(ts: float | None) -> str:
    if ts is None:
        return "unknown"
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    return f"{int(delta / 86400)}d ago"
