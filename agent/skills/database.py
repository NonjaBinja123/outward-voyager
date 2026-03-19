import json
import sqlite3
from pathlib import Path

from .schema import Skill, SCOPE_GAME_SPECIFIC, SCOPE_CROSS_GAME, SCOPE_ARCHIVED


class SkillDatabase:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()
        self._migrate()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                action_type TEXT NOT NULL,
                parameters TEXT NOT NULL,
                preconditions TEXT NOT NULL,
                tags TEXT NOT NULL,
                success_rate REAL DEFAULT 1.0,
                times_used INTEGER DEFAULT 0,
                times_succeeded INTEGER DEFAULT 0,
                description TEXT DEFAULT '',
                game_scope TEXT NOT NULL DEFAULT 'game_specific',
                source_game_id TEXT
            )
        """)
        self._conn.commit()

    def _migrate(self) -> None:
        """Add Phase 9 columns to existing databases that predate them."""
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(skills)").fetchall()
        }
        if "game_scope" not in existing:
            self._conn.execute(
                "ALTER TABLE skills ADD COLUMN game_scope TEXT NOT NULL DEFAULT 'game_specific'"
            )
        if "source_game_id" not in existing:
            self._conn.execute(
                "ALTER TABLE skills ADD COLUMN source_game_id TEXT"
            )
        self._conn.commit()

    def upsert(self, skill: Skill) -> Skill:
        cur = self._conn.execute("""
            INSERT INTO skills (name, action_type, parameters, preconditions, tags,
                                success_rate, times_used, times_succeeded, description,
                                game_scope, source_game_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                action_type=excluded.action_type,
                parameters=excluded.parameters,
                preconditions=excluded.preconditions,
                tags=excluded.tags,
                success_rate=excluded.success_rate,
                times_used=excluded.times_used,
                times_succeeded=excluded.times_succeeded,
                description=excluded.description,
                game_scope=excluded.game_scope,
                source_game_id=excluded.source_game_id
        """, (
            skill.name, skill.action_type,
            json.dumps(skill.parameters), json.dumps(skill.preconditions),
            json.dumps(skill.tags), skill.success_rate,
            skill.times_used, skill.times_succeeded, skill.description,
            skill.game_scope, skill.source_game_id,
        ))
        self._conn.commit()
        skill.id = cur.lastrowid
        return skill

    def get_by_name(self, name: str) -> Skill | None:
        row = self._conn.execute(
            "SELECT * FROM skills WHERE name=?", (name,)
        ).fetchone()
        return self._row_to_skill(row) if row else None

    def get_by_tag(self, tag: str) -> list[Skill]:
        rows = self._conn.execute(
            "SELECT * FROM skills WHERE tags LIKE ?", (f'%"{tag}"%',)
        ).fetchall()
        return [self._row_to_skill(r) for r in rows]

    def get_for_game(self, game_id: str) -> list[Skill]:
        """
        Return skills usable in the given game:
          - All 'cross_game' skills
          - 'game_specific' skills whose source_game_id matches
        Never returns 'archived' skills.
        """
        rows = self._conn.execute("""
            SELECT * FROM skills
            WHERE game_scope = ?
               OR (game_scope = ? AND (source_game_id = ? OR source_game_id IS NULL))
        """, (SCOPE_CROSS_GAME, SCOPE_GAME_SPECIFIC, game_id)).fetchall()
        return [self._row_to_skill(r) for r in rows]

    def archive_game_specific(self, source_game_id: str) -> int:
        """
        Mark all game_specific skills from source_game_id as archived.
        Called when migrating to a new game. Returns count of archived skills.
        """
        cur = self._conn.execute("""
            UPDATE skills
            SET game_scope = ?
            WHERE game_scope = ? AND source_game_id = ?
        """, (SCOPE_ARCHIVED, SCOPE_GAME_SPECIFIC, source_game_id))
        self._conn.commit()
        return cur.rowcount

    def get_failing(self, threshold: float = 0.3) -> list[Skill]:
        """Return skills with success rate below threshold and at least 3 uses."""
        rows = self._conn.execute(
            "SELECT * FROM skills WHERE times_used >= 3 AND success_rate < ?",
            (threshold,)
        ).fetchall()
        return [self._row_to_skill(r) for r in rows]

    def delete(self, name: str) -> None:
        self._conn.execute("DELETE FROM skills WHERE name=?", (name,))
        self._conn.commit()

    def _row_to_skill(self, row: tuple) -> Skill:
        # Columns: id, name, action_type, parameters, preconditions, tags,
        #          success_rate, times_used, times_succeeded, description,
        #          game_scope, source_game_id
        return Skill(
            id=row[0], name=row[1], action_type=row[2],
            parameters=json.loads(row[3]), preconditions=json.loads(row[4]),
            tags=json.loads(row[5]), success_rate=row[6],
            times_used=row[7], times_succeeded=row[8], description=row[9],
            game_scope=row[10] if len(row) > 10 else SCOPE_GAME_SPECIFIC,
            source_game_id=row[11] if len(row) > 11 else None,
        )
