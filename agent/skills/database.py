import json
import sqlite3
from pathlib import Path

from .schema import Skill


class SkillDatabase:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()

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
                description TEXT DEFAULT ''
            )
        """)
        self._conn.commit()

    def upsert(self, skill: Skill) -> Skill:
        cur = self._conn.execute("""
            INSERT INTO skills (name, action_type, parameters, preconditions, tags,
                                success_rate, times_used, times_succeeded, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                action_type=excluded.action_type,
                parameters=excluded.parameters,
                preconditions=excluded.preconditions,
                tags=excluded.tags,
                success_rate=excluded.success_rate,
                times_used=excluded.times_used,
                times_succeeded=excluded.times_succeeded,
                description=excluded.description
        """, (
            skill.name, skill.action_type,
            json.dumps(skill.parameters), json.dumps(skill.preconditions),
            json.dumps(skill.tags), skill.success_rate,
            skill.times_used, skill.times_succeeded, skill.description,
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
        rows = self._conn.execute("SELECT * FROM skills WHERE tags LIKE ?", (f'%"{tag}"%',)).fetchall()
        return [self._row_to_skill(r) for r in rows]

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
        return Skill(
            id=row[0], name=row[1], action_type=row[2],
            parameters=json.loads(row[3]), preconditions=json.loads(row[4]),
            tags=json.loads(row[5]), success_rate=row[6],
            times_used=row[7], times_succeeded=row[8], description=row[9],
        )
