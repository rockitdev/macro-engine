"""SQLite storage for macro-engine.

One file at ~/.local/share/macro-engine/macros.db (override with MACRO_ENGINE_DB).
Schema is idempotent; connect() applies it on every open.
"""

import os
import sqlite3
from pathlib import Path


def default_db_path() -> Path:
    env = os.environ.get("MACRO_ENGINE_DB")
    if env:
        return Path(env)
    return Path.home() / ".local/share/macro-engine/macros.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS foods (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    brand     TEXT,
    -- fdc_foundation | fdc_sr_legacy | fdc_survey | off | scrape:<chain> | recipe | manual
    source    TEXT NOT NULL,
    source_id TEXT,
    verified  INTEGER NOT NULL DEFAULT 1,
    UNIQUE(source, source_id)
);

-- All values per 100 g
CREATE TABLE IF NOT EXISTS food_nutrients (
    food_id   INTEGER PRIMARY KEY REFERENCES foods(id) ON DELETE CASCADE,
    kcal      REAL,
    protein_g REAL,
    carb_g    REAL,
    fat_g     REAL,
    fiber_g   REAL,
    sugar_g   REAL,
    satfat_g  REAL,
    sodium_mg REAL
);

CREATE TABLE IF NOT EXISTS portions (
    id      INTEGER PRIMARY KEY,
    food_id INTEGER NOT NULL REFERENCES foods(id) ON DELETE CASCADE,
    label   TEXT NOT NULL,
    grams   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_portions_food ON portions(food_id);

-- Ryan's phrases -> a specific food (+ optional default grams). Latest row wins.
CREATE TABLE IF NOT EXISTS aliases (
    id            INTEGER PRIMARY KEY,
    phrase        TEXT NOT NULL,
    food_id       INTEGER NOT NULL REFERENCES foods(id) ON DELETE CASCADE,
    default_grams REAL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_aliases_phrase ON aliases(phrase);

CREATE TABLE IF NOT EXISTS log (
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
CREATE INDEX IF NOT EXISTS idx_log_date ON log(date);

-- Latest row (by effective_date, then id) at or before a given date wins.
CREATE TABLE IF NOT EXISTS targets (
    id             INTEGER PRIMARY KEY,
    effective_date TEXT NOT NULL,
    kcal           REAL NOT NULL,
    protein_g      REAL NOT NULL,
    carb_g         REAL NOT NULL,
    fat_g          REAL NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS food_fts USING fts5(name, brand, content='', tokenize='porter unicode61');
"""


def connect(db_path=None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


def rebuild_fts(con: sqlite3.Connection) -> None:
    """Drop and rebuild the contentless FTS index from foods (contentless
    tables can't delete rows, so ETL re-runs rebuild instead)."""
    con.execute("DROP TABLE IF EXISTS food_fts")
    con.execute("CREATE VIRTUAL TABLE food_fts USING fts5(name, brand, content='', tokenize='porter unicode61')")
    con.execute(
        "INSERT INTO food_fts(rowid, name, brand) "
        "SELECT id, name, COALESCE(brand, '') FROM foods"
    )
    con.commit()
