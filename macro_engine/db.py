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
    -- where Ryan buys it ("Costco") — people remember store, not brand
    store     TEXT,
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
    estimated INTEGER NOT NULL DEFAULT 0,
    -- breakfast | lunch | dinner | snacks; null on rows logged before meals
    meal      TEXT
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
    fiber_g        REAL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Preferences. week_starts_on is a weekday name; absent means monday.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS food_fts USING fts5(name, brand, store, content='', tokenize='porter unicode61');

-- One row per day: morning weigh-in plus optional body-composition checks.
-- Always metric (kg / cm); the boundary converts lb / in.
CREATE TABLE IF NOT EXISTS body_metrics (
    date          TEXT PRIMARY KEY,
    weight_kg     REAL,
    body_fat_pct  REAL,
    waist_cm      REAL,
    hips_cm       REAL,
    note          TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Import provenance is per field so separate weight/body-fat batches can merge.
CREATE TABLE IF NOT EXISTS health_import_metrics (
    date TEXT NOT NULL,
    field TEXT NOT NULL,
    sample_ts REAL NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (date, field)
);

-- Apple Watch activity (macro_engine/activity.py). Context only: nothing here
-- feeds targets or maintenance. Daily step totals are replacement snapshots
-- per source; the selected source is chosen in settings (activity_source).
CREATE TABLE IF NOT EXISTS health_step_days (
    date         TEXT NOT NULL,
    source       TEXT NOT NULL,
    policy       TEXT NOT NULL,
    steps        INTEGER NOT NULL CHECK (steps >= 0),
    sample_count INTEGER NOT NULL,
    captured_at  TEXT NOT NULL,
    captured_ts  REAL NOT NULL,
    received_at  TEXT NOT NULL,
    PRIMARY KEY (date, source, policy)
);

-- Latest dedicated resting-heart-rate estimate per local day and source.
CREATE TABLE IF NOT EXISTS health_rhr_days (
    date        TEXT NOT NULL,
    source      TEXT NOT NULL,
    bpm         REAL NOT NULL,
    sample_at   TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    captured_ts REAL NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (date, source)
);

-- Individual sessions. identity_basis: external | derived | manual.
CREATE TABLE IF NOT EXISTS health_workouts (
    id               INTEGER PRIMARY KEY,
    identity_key     TEXT NOT NULL UNIQUE,
    identity_basis   TEXT NOT NULL,
    external_id      TEXT,
    source           TEXT NOT NULL,
    type             TEXT NOT NULL,
    category         TEXT NOT NULL,
    local_date       TEXT NOT NULL,
    start_at         TEXT,
    end_at           TEXT,
    duration_seconds INTEGER NOT NULL CHECK (duration_seconds > 0),
    duration_basis   TEXT NOT NULL,
    note             TEXT,
    captured_at      TEXT,
    captured_ts      REAL,
    received_at      TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_health_workouts_date ON health_workouts(local_date);

-- Per-category/day acknowledgements. queried_* = last accepted query (drives
-- catch-up); attempt_* = last attempt of any status. Data coverage is read
-- from the value tables, so an accepted empty query is not an observation.
CREATE TABLE IF NOT EXISTS health_activity_coverage (
    category            TEXT NOT NULL,
    date                TEXT NOT NULL,
    queried_captured_at TEXT,
    queried_ts          REAL,
    queried_received_at TEXT,
    attempt_status      TEXT,
    attempt_captured_at TEXT,
    attempt_ts          REAL,
    attempt_received_at TEXT,
    PRIMARY KEY (category, date)
);

-- Diet phases (cut | maintenance | reverse | diet_break). The latest phase
-- starting at or before a date is the active one. params is JSON.
CREATE TABLE IF NOT EXISTS phases (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    params      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Weekly check-ins: what the coach saw, what it recommended, and whether the
-- change was applied. Applied carb/fat adjustments are how the coach learns
-- the user's personal plateau-breaking threshold.
CREATE TABLE IF NOT EXISTS checkins (
    id             INTEGER PRIMARY KEY,
    date           TEXT NOT NULL,
    week_start     TEXT,
    phase_id       INTEGER,
    status         TEXT NOT NULL,
    action         TEXT NOT NULL,
    adjust_pct     REAL,
    activity_pct   REAL,
    applied        INTEGER NOT NULL DEFAULT 0,
    payload        TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_checkins_date ON checkins(date);
"""


def _migrate(con: sqlite3.Connection) -> None:
    cols = {r["name"] for r in con.execute("PRAGMA table_info(foods)")}
    if "store" not in cols:
        con.execute("ALTER TABLE foods ADD COLUMN store TEXT")
        rebuild_fts(con)  # FTS gained the store column
    tcols = {r["name"] for r in con.execute("PRAGMA table_info(targets)")}
    if "fiber_g" not in tcols:
        con.execute("ALTER TABLE targets ADD COLUMN fiber_g REAL")
    lcols = {r["name"] for r in con.execute("PRAGMA table_info(log)")}
    if lcols and "meal" not in lcols:
        con.execute("ALTER TABLE log ADD COLUMN meal TEXT")


def connect(db_path=None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def rebuild_fts(con: sqlite3.Connection) -> None:
    """Drop and rebuild the contentless FTS index from foods (contentless
    tables can't delete rows, so ETL re-runs rebuild instead)."""
    con.execute("DROP TABLE IF EXISTS food_fts")
    con.execute("CREATE VIRTUAL TABLE food_fts USING fts5(name, brand, store, content='', tokenize='porter unicode61')")
    con.execute(
        "INSERT INTO food_fts(rowid, name, brand, store) "
        "SELECT id, name, COALESCE(brand, ''), COALESCE(store, '') FROM foods"
    )
    con.commit()
