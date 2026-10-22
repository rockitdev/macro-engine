"""Extra coverage for macro_engine/db.py — migration branches and path helpers.

The existing test_macro_engine.py covers connect(), rebuild_fts(), and the
happy schema path.  This file covers the two ALTER TABLE migration branches
(_migrate) and the MACRO_ENGINE_DB env-var override in default_db_path().
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro_engine import db


class TestDefaultDbPath(unittest.TestCase):
    """default_db_path() should respect MACRO_ENGINE_DB when set."""

    def test_env_var_overrides_default(self):
        custom = "/tmp/custom-macro.db"
        old = os.environ.get("MACRO_ENGINE_DB")
        os.environ["MACRO_ENGINE_DB"] = custom
        try:
            self.assertEqual(str(db.default_db_path()), custom)
        finally:
            if old is None:
                os.environ.pop("MACRO_ENGINE_DB", None)
            else:
                os.environ["MACRO_ENGINE_DB"] = old

    def test_home_fallback_when_env_unset(self):
        old = os.environ.pop("MACRO_ENGINE_DB", None)
        try:
            path = db.default_db_path()
            self.assertIn("macro-engine", str(path))
            self.assertTrue(str(path).endswith("macros.db"))
        finally:
            if old is not None:
                os.environ["MACRO_ENGINE_DB"] = old


class TestMigrateStoreColumn(unittest.TestCase):
    """_migrate() adds the 'store' column to an older foods table."""

    def _old_schema(self, path: Path) -> sqlite3.Connection:
        """Create a pre-migration DB: foods table WITHOUT store column."""
        con = sqlite3.connect(str(path))
        con.row_factory = sqlite3.Row
        con.executescript("""
            CREATE TABLE foods (
                id        INTEGER PRIMARY KEY,
                name      TEXT NOT NULL,
                brand     TEXT,
                source    TEXT NOT NULL,
                source_id TEXT,
                verified  INTEGER NOT NULL DEFAULT 1,
                UNIQUE(source, source_id)
            );
            CREATE TABLE food_nutrients (
                food_id   INTEGER PRIMARY KEY REFERENCES foods(id),
                kcal      REAL, protein_g REAL, carb_g REAL,
                fat_g     REAL, fiber_g   REAL
            );
            CREATE TABLE portions (
                id      INTEGER PRIMARY KEY,
                food_id INTEGER NOT NULL REFERENCES foods(id),
                label   TEXT NOT NULL,
                grams   REAL NOT NULL
            );
            CREATE TABLE aliases (
                id            INTEGER PRIMARY KEY,
                phrase        TEXT NOT NULL,
                food_id       INTEGER NOT NULL REFERENCES foods(id),
                default_grams REAL,
                created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE log (
                id        INTEGER PRIMARY KEY,
                ts        TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                date      TEXT NOT NULL,
                food_id   INTEGER REFERENCES foods(id),
                qty_desc  TEXT,
                grams     REAL,
                kcal      REAL NOT NULL DEFAULT 0,
                protein_g REAL NOT NULL DEFAULT 0,
                carb_g    REAL NOT NULL DEFAULT 0,
                fat_g     REAL NOT NULL DEFAULT 0,
                fiber_g   REAL NOT NULL DEFAULT 0,
                raw_text  TEXT,
                estimated INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE targets (
                id             INTEGER PRIMARY KEY,
                effective_date TEXT NOT NULL,
                kcal           REAL NOT NULL,
                protein_g      REAL NOT NULL,
                carb_g         REAL NOT NULL,
                fat_g          REAL NOT NULL,
                created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            CREATE VIRTUAL TABLE food_fts
                USING fts5(name, brand, content='', tokenize='porter unicode61');
        """)
        con.commit()
        return con

    def test_migrate_adds_store_column_to_foods(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            con = self._old_schema(path)
            cols_before = {r["name"] for r in con.execute("PRAGMA table_info(foods)")}
            self.assertNotIn("store", cols_before)

            db._migrate(con)

            cols_after = {r["name"] for r in con.execute("PRAGMA table_info(foods)")}
            self.assertIn("store", cols_after)
            con.close()

    def test_migrate_adds_fiber_g_column_to_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            con = self._old_schema(path)
            tcols_before = {r["name"] for r in con.execute("PRAGMA table_info(targets)")}
            self.assertNotIn("fiber_g", tcols_before)

            db._migrate(con)

            tcols_after = {r["name"] for r in con.execute("PRAGMA table_info(targets)")}
            self.assertIn("fiber_g", tcols_after)
            con.close()

    def test_connect_applies_migration_on_old_db(self):
        """db.connect() must leave an old schema fully migrated."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            # Build old schema with raw sqlite3 (bypass db.connect's SCHEMA)
            old_con = self._old_schema(path)
            old_con.close()

            # Now open via db.connect(), which runs executescript + _migrate
            con = db.connect(path)
            cols = {r["name"] for r in con.execute("PRAGMA table_info(foods)")}
            tcols = {r["name"] for r in con.execute("PRAGMA table_info(targets)")}
            lcols = {r["name"] for r in con.execute("PRAGMA table_info(log)")}
            self.assertIn("store", cols)
            self.assertIn("fiber_g", tcols)
            self.assertIn("meal", lcols)
            con.close()

    def test_migrate_is_idempotent_on_current_schema(self):
        """Running _migrate on a fresh schema must not raise."""
        with tempfile.TemporaryDirectory() as tmp:
            con = db.connect(Path(tmp) / "fresh.db")
            # Should be a no-op without errors
            db._migrate(con)
            cols = {r["name"] for r in con.execute("PRAGMA table_info(foods)")}
            self.assertIn("store", cols)
            con.close()


class TestRebuildFts(unittest.TestCase):
    """rebuild_fts() must index existing foods including the store field."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.con = db.connect(Path(self.tmp.name) / "fts.db")

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def test_rebuild_fts_indexes_store(self):
        fid = self.con.execute(
            "INSERT INTO foods (name, source, store) VALUES ('Widget', 'manual', 'Costco') RETURNING id"
        ).fetchone()[0]
        self.con.execute(
            "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g) VALUES (?, 100, 5, 10, 3)",
            (fid,))
        self.con.commit()
        db.rebuild_fts(self.con)

        # FTS should now find the food via its store name
        rows = self.con.execute(
            "SELECT rowid FROM food_fts WHERE food_fts MATCH 'costco'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], fid)

    def test_rebuild_fts_clears_previous_index(self):
        """A second rebuild must not duplicate rows."""
        fid = self.con.execute(
            "INSERT INTO foods (name, source) VALUES ('Apple', 'manual') RETURNING id"
        ).fetchone()[0]
        self.con.execute(
            "INSERT INTO food_nutrients (food_id, kcal, protein_g, carb_g, fat_g) VALUES (?, 52, 0, 14, 0)",
            (fid,))
        self.con.commit()
        db.rebuild_fts(self.con)
        db.rebuild_fts(self.con)  # second call

        rows = self.con.execute(
            "SELECT rowid FROM food_fts WHERE food_fts MATCH 'apple'"
        ).fetchall()
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
