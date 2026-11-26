"""SQLite storage. Everything lives in one file: data/forge.db."""
import os
import sqlite3
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, "data", "forge.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- kind: weight_reps | bodyweight_reps | weighted_bodyweight | assisted_bodyweight
--       | reps_only | duration | weight_duration | distance_duration
CREATE TABLE IF NOT EXISTS exercises (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  equipment TEXT DEFAULT '',
  muscle TEXT DEFAULT '',
  secondary TEXT DEFAULT '',
  kind TEXT DEFAULT 'weight_reps',
  image_url TEXT DEFAULT '',
  image_url2 TEXT DEFAULT '',
  instructions TEXT DEFAULT '',
  custom INTEGER DEFAULT 0,
  archived INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ex_name ON exercises(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS folders (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  position INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS routines (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
  notes TEXT DEFAULT '',
  position INTEGER DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS routine_exercises (
  id INTEGER PRIMARY KEY,
  routine_id INTEGER NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
  exercise_id INTEGER NOT NULL REFERENCES exercises(id),
  position INTEGER DEFAULT 0,
  superset INTEGER,
  rest_seconds INTEGER,
  rep_min INTEGER,
  rep_max INTEGER,
  notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS routine_sets (
  id INTEGER PRIMARY KEY,
  routine_exercise_id INTEGER NOT NULL REFERENCES routine_exercises(id) ON DELETE CASCADE,
  position INTEGER DEFAULT 0,
  set_type TEXT DEFAULT 'normal',
  weight REAL,
  reps INTEGER,
  duration INTEGER,
  distance REAL
);

-- ended_at NULL = workout in progress
CREATE TABLE IF NOT EXISTS workouts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  routine_id INTEGER REFERENCES routines(id) ON DELETE SET NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  notes TEXT DEFAULT '',
  source TEXT DEFAULT 'forge',
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS w_started ON workouts(started_at);

CREATE TABLE IF NOT EXISTS workout_exercises (
  id INTEGER PRIMARY KEY,
  workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
  exercise_id INTEGER NOT NULL REFERENCES exercises(id),
  position INTEGER DEFAULT 0,
  superset INTEGER,
  rest_seconds INTEGER,
  notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS we_ex ON workout_exercises(exercise_id);

-- set_type: normal | warmup | drop | failure
CREATE TABLE IF NOT EXISTS workout_sets (
  id INTEGER PRIMARY KEY,
  workout_exercise_id INTEGER NOT NULL REFERENCES workout_exercises(id) ON DELETE CASCADE,
  position INTEGER DEFAULT 0,
  set_type TEXT DEFAULT 'normal',
  weight REAL,
  reps INTEGER,
  rpe REAL,
  duration INTEGER,
  distance REAL,
  done INTEGER DEFAULT 0
);

-- Heart rate samples from Apple Health (unix seconds, UTC)
CREATE TABLE IF NOT EXISTS hr_samples (
  ts INTEGER PRIMARY KEY,
  bpm REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS resting_hr (
  day TEXT PRIMARY KEY,
  bpm REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS bodyweight (
  day TEXT PRIMARY KEY,
  weight REAL NOT NULL
);
"""

_local = threading.local()
_path = DEFAULT_PATH


def set_path(path):
    global _path
    _path = path
    _local.__dict__.clear()


def conn():
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != _path:
        os.makedirs(os.path.dirname(_path), exist_ok=True)
        c = sqlite3.connect(_path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
        _local.path = _path
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    c.commit()
    from . import seed
    seed.ensure(c)


def rows(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def get_setting(key, default=None):
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    c = conn()
    c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
              (key, None if value is None else str(value)))
    c.commit()
