"""
Tests for SkillDatabase game_scope and migration features (Phase 9).
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from skills.database import SkillDatabase
from skills.schema import Skill, SCOPE_CROSS_GAME, SCOPE_GAME_SPECIFIC, SCOPE_ARCHIVED


def _make_skill(name: str, scope: str = SCOPE_GAME_SPECIFIC, game_id: str | None = "outward_definitive") -> Skill:
    return Skill(
        id=None,
        name=name,
        action_type="test",
        parameters={},
        preconditions={},
        tags=["test"],
        game_scope=scope,
        source_game_id=game_id,
    )


class TestSkillDatabaseGameScope(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(self._tmp.name) / "skills.db")
        self._db = SkillDatabase(db_path)

    def tearDown(self) -> None:
        self._db._conn.close()
        self._tmp.cleanup()

    def test_upsert_and_get_by_name(self) -> None:
        s = _make_skill("move_to_target", SCOPE_GAME_SPECIFIC, "outward_definitive")
        self._db.upsert(s)
        result = self._db.get_by_name("move_to_target")
        self.assertIsNotNone(result)
        self.assertEqual(result.game_scope, SCOPE_GAME_SPECIFIC)
        self.assertEqual(result.source_game_id, "outward_definitive")

    def test_upsert_cross_game_skill(self) -> None:
        s = _make_skill("scan_area", SCOPE_CROSS_GAME, None)
        self._db.upsert(s)
        result = self._db.get_by_name("scan_area")
        self.assertIsNotNone(result)
        self.assertEqual(result.game_scope, SCOPE_CROSS_GAME)
        self.assertIsNone(result.source_game_id)

    def test_get_for_game_includes_cross_game(self) -> None:
        self._db.upsert(_make_skill("cross_skill", SCOPE_CROSS_GAME, None))
        self._db.upsert(_make_skill("outward_skill", SCOPE_GAME_SPECIFIC, "outward_definitive"))
        self._db.upsert(_make_skill("other_game_skill", SCOPE_GAME_SPECIFIC, "other_game"))

        results = self._db.get_for_game("outward_definitive")
        names = {r.name for r in results}

        self.assertIn("cross_skill", names)
        self.assertIn("outward_skill", names)
        self.assertNotIn("other_game_skill", names)

    def test_get_for_game_excludes_archived(self) -> None:
        self._db.upsert(_make_skill("archived_skill", SCOPE_ARCHIVED, "outward_definitive"))
        self._db.upsert(_make_skill("active_skill", SCOPE_GAME_SPECIFIC, "outward_definitive"))

        results = self._db.get_for_game("outward_definitive")
        names = {r.name for r in results}

        self.assertNotIn("archived_skill", names)
        self.assertIn("active_skill", names)

    def test_get_for_game_game_specific_with_null_source_included(self) -> None:
        """Skills from before portability (source_game_id=None) should load for any game."""
        self._db.upsert(_make_skill("legacy_skill", SCOPE_GAME_SPECIFIC, None))
        results = self._db.get_for_game("outward_definitive")
        names = {r.name for r in results}
        self.assertIn("legacy_skill", names)

    def test_archive_game_specific(self) -> None:
        self._db.upsert(_make_skill("skill_a", SCOPE_GAME_SPECIFIC, "outward_definitive"))
        self._db.upsert(_make_skill("skill_b", SCOPE_GAME_SPECIFIC, "outward_definitive"))
        self._db.upsert(_make_skill("cross_skill", SCOPE_CROSS_GAME, None))

        count = self._db.archive_game_specific("outward_definitive")
        self.assertEqual(count, 2)

        # After archiving, game_specific skills become archived
        results = self._db.get_for_game("outward_definitive")
        names = {r.name for r in results}
        self.assertNotIn("skill_a", names)
        self.assertNotIn("skill_b", names)
        self.assertIn("cross_skill", names)

    def test_archive_game_specific_other_game_unaffected(self) -> None:
        self._db.upsert(_make_skill("outward_skill", SCOPE_GAME_SPECIFIC, "outward_definitive"))
        self._db.upsert(_make_skill("other_skill", SCOPE_GAME_SPECIFIC, "other_game"))

        self._db.archive_game_specific("outward_definitive")

        # other_game skill should still be active
        results = self._db.get_for_game("other_game")
        names = {r.name for r in results}
        self.assertIn("other_skill", names)

    def test_upsert_updates_scope(self) -> None:
        """Upserting an existing skill with new scope should update it."""
        s = _make_skill("evolving_skill", SCOPE_GAME_SPECIFIC, "outward_definitive")
        self._db.upsert(s)

        s2 = _make_skill("evolving_skill", SCOPE_CROSS_GAME, None)
        self._db.upsert(s2)

        result = self._db.get_by_name("evolving_skill")
        self.assertEqual(result.game_scope, SCOPE_CROSS_GAME)
        self.assertIsNone(result.source_game_id)


class TestSkillDatabaseMigration(unittest.TestCase):
    """Tests that existing databases (without game_scope columns) are migrated correctly."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmp.name) / "old_skills.db")
        self._dbs: list = []  # Track open connections for cleanup

    def tearDown(self) -> None:
        for db in self._dbs:
            db._conn.close()
        self._tmp.cleanup()

    def _create_old_schema_db(self) -> None:
        """Simulate an existing DB from before Phase 9 (no game_scope column)."""
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE skills (
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
        conn.execute("""
            INSERT INTO skills (name, action_type, parameters, preconditions, tags, description)
            VALUES ('old_skill', 'move', '{}', '{}', '["navigate"]', 'An old skill')
        """)
        conn.commit()
        conn.close()

    def test_migration_adds_columns(self) -> None:
        self._create_old_schema_db()
        db = SkillDatabase(self._db_path)  # Should auto-migrate
        self._dbs.append(db)

        result = db.get_by_name("old_skill")
        self.assertIsNotNone(result)
        # Migration should default old skills to game_specific/no source_game_id
        self.assertEqual(result.game_scope, SCOPE_GAME_SPECIFIC)
        self.assertIsNone(result.source_game_id)

    def test_migration_allows_new_upserts(self) -> None:
        self._create_old_schema_db()
        db = SkillDatabase(self._db_path)
        self._dbs.append(db)

        new_skill = _make_skill("new_skill", SCOPE_CROSS_GAME, None)
        db.upsert(new_skill)

        result = db.get_by_name("new_skill")
        self.assertEqual(result.game_scope, SCOPE_CROSS_GAME)

    def test_migration_is_idempotent(self) -> None:
        """Running migration twice should not raise errors."""
        self._create_old_schema_db()
        db1 = SkillDatabase(self._db_path)  # First migration
        self._dbs.append(db1)
        db1._conn.close()
        db2 = SkillDatabase(self._db_path)  # Second migration (should be no-op)
        self._dbs.append(db2)

        result = db2.get_by_name("old_skill")
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
