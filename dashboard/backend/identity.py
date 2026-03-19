"""
IdentityManager — stable UUID identities for Voyager users.

Bridges in-game player names, dashboard session tokens, and persistent
relationship records. Every unique identity gets one stable UUID that
persists across sessions in data/identity.json.

Sources of identity:
  - In-game chat: identified by player name (string from game)
  - Dashboard: identified by session token (cookie set on first visit)

These can be linked manually or via the /api/identity/link endpoint.
"""

import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class UserIdentity:
    id: str
    display_name: str
    in_game_names: list[str]
    session_tokens: list[str]
    first_seen: float
    last_seen: float
    notes: str = ""


class IdentityManager:
    """Manages stable UUID identities for Voyager users across sessions.

    Persists identity records to data/identity.json, keyed by UUID string.
    Identities can be looked up by in-game player name or session token,
    and two separate identities can be merged into one.
    """

    def __init__(self, data_dir: str = "./data") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._identity_file = self._data_dir / "identity.json"
        self._identities: dict[str, UserIdentity] = {}
        self._load()
        logger.info(
            "IdentityManager loaded %d identities from %s",
            len(self._identities),
            self._identity_file,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_create_by_game_name(self, name: str) -> UserIdentity:
        """Return the identity associated with this in-game player name.

        If no identity has this name, a new one is created with the name as
        both the display_name and the sole entry in in_game_names.
        Updates last_seen on every call.
        """
        for identity in self._identities.values():
            if name in identity.in_game_names:
                identity.last_seen = time.time()
                self._save()
                return identity

        identity = self._create(display_name=name, in_game_names=[name])
        logger.info("Created new identity %s for in-game name %r", identity.id, name)
        return identity

    def get_or_create_by_token(self, token: str) -> UserIdentity:
        """Return the identity associated with this session token.

        If no identity holds this token, a new one is created. Updates
        last_seen on every call.
        """
        for identity in self._identities.values():
            if token in identity.session_tokens:
                identity.last_seen = time.time()
                self._save()
                return identity

        identity = self._create(
            display_name=f"dashboard-{token[:8]}",
            session_tokens=[token],
        )
        logger.info(
            "Created new identity %s for session token %s…", identity.id, token[:8]
        )
        return identity

    def generate_token(self) -> str:
        """Generate a new cryptographically random session token."""
        return secrets.token_urlsafe(32)

    def get_by_id(self, user_id: str) -> Optional[UserIdentity]:
        """Return the identity with the given UUID, or None if not found."""
        return self._identities.get(user_id)

    def link(self, user_id_a: str, user_id_b: str) -> UserIdentity:
        """Merge two identities into one, keeping the lower-sorted UUID.

        All in_game_names and session_tokens from both records are combined
        (deduplicated). The identity with the higher-sorted UUID is deleted.
        Returns the surviving merged identity.
        """
        id_a = self._identities.get(user_id_a)
        id_b = self._identities.get(user_id_b)

        if id_a is None:
            raise KeyError(f"Identity {user_id_a!r} not found")
        if id_b is None:
            raise KeyError(f"Identity {user_id_b!r} not found")
        if user_id_a == user_id_b:
            return id_a

        # Keep the lexicographically smaller UUID for stability.
        if user_id_a < user_id_b:
            survivor, donor = id_a, id_b
        else:
            survivor, donor = id_b, id_a

        # Merge names and tokens, preserving order while deduplicating.
        combined_names = list(dict.fromkeys(survivor.in_game_names + donor.in_game_names))
        combined_tokens = list(
            dict.fromkeys(survivor.session_tokens + donor.session_tokens)
        )

        survivor.in_game_names = combined_names
        survivor.session_tokens = combined_tokens
        survivor.first_seen = min(survivor.first_seen, donor.first_seen)
        survivor.last_seen = max(survivor.last_seen, donor.last_seen)
        if donor.notes and donor.notes not in survivor.notes:
            survivor.notes = (
                f"{survivor.notes}\n{donor.notes}".strip()
            )

        # Remove the donor identity.
        del self._identities[donor.id]
        self._save()

        logger.info(
            "Merged identity %s into %s (deleted %s)",
            donor.id,
            survivor.id,
            donor.id,
        )
        return survivor

    def all_identities(self) -> list[UserIdentity]:
        """Return a snapshot list of all known identities."""
        return list(self._identities.values())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Persist all identities to data/identity.json."""
        serialized = {uid: asdict(identity) for uid, identity in self._identities.items()}
        tmp_path = self._identity_file.with_suffix(".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(serialized, f, indent=2)
            tmp_path.replace(self._identity_file)
        except OSError as exc:
            logger.error("Failed to save identity store: %s", exc)
            tmp_path.unlink(missing_ok=True)

    def _load(self) -> None:
        """Load identities from data/identity.json (if it exists)."""
        if not self._identity_file.exists():
            return
        try:
            with self._identity_file.open("r", encoding="utf-8") as f:
                raw: dict = json.load(f)
            for uid, data in raw.items():
                self._identities[uid] = UserIdentity(**data)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.error("Failed to load identity store: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create(
        self,
        display_name: str,
        in_game_names: Optional[list[str]] = None,
        session_tokens: Optional[list[str]] = None,
    ) -> UserIdentity:
        now = time.time()
        identity = UserIdentity(
            id=str(uuid.uuid4()),
            display_name=display_name,
            in_game_names=in_game_names or [],
            session_tokens=session_tokens or [],
            first_seen=now,
            last_seen=now,
        )
        self._identities[identity.id] = identity
        self._save()
        return identity
