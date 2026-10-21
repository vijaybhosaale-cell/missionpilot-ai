"""SQLite storage. Numbers, dates (ISO, UTC), money (integer minor units) and status
codes are stored language-independently; text is localized only for display.
Template-generated text is stored as translation keys (title_key) so it shows in
every language; user/AI-written text is stored in the project's canonical
language (title_text) with optional rows in content_translations.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(os.environ.get("MISSIONPILOT_DB", Path(__file__).resolve().parent.parent / "data" / "missionpilot.db"))

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  interface_locale TEXT NOT NULL DEFAULT 'en',
  ai_locale TEXT,
  doc_locale TEXT,
  report_locale TEXT,
  date_style TEXT NOT NULL DEFAULT 'medium',
  time_zone TEXT NOT NULL DEFAULT 'Europe/Paris',
  week_start INTEGER NOT NULL DEFAULT 0,
  voice_enabled INTEGER NOT NULL DEFAULT 1,
  voice_name TEXT,
  voice_rate REAL NOT NULL DEFAULT 1.0,
  onboarding_completed_at TEXT,
  onboarding_step INTEGER NOT NULL DEFAULT 0,
  is_demo INTEGER NOT NULL DEFAULT 0,
  current_project_id INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  name_key TEXT,
  description TEXT NOT NULL DEFAULT '',
  description_key TEXT,
  project_type TEXT NOT NULL DEFAULT 'software',
  canonical_locale TEXT NOT NULL DEFAULT 'en',
  currency TEXT NOT NULL DEFAULT 'EUR',
  budget_minor INTEGER NOT NULL DEFAULT 0,
  team_size INTEGER NOT NULL DEFAULT 3,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  engine TEXT NOT NULL DEFAULT 'template',
  status TEXT NOT NULL DEFAULT 'active',
  answers_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  role_key TEXT NOT NULL,
  email TEXT NOT NULL DEFAULT '',
  capacity_pct INTEGER NOT NULL DEFAULT 100,
  daily_rate_minor INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS phases (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  code TEXT NOT NULL,
  name_key TEXT,
  name_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  phase_id INTEGER NOT NULL REFERENCES phases(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  wbs TEXT NOT NULL,
  code TEXT NOT NULL,
  title_key TEXT,
  title_text TEXT NOT NULL DEFAULT '',
  desc_key TEXT,
  desc_text TEXT NOT NULL DEFAULT '',
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  duration_days INTEGER NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'todo',
  priority TEXT NOT NULL DEFAULT 'medium',
  assignee_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
  is_milestone INTEGER NOT NULL DEFAULT 0,
  effort_hours INTEGER NOT NULL DEFAULT 0,
  deliverable TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dependencies (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  pred_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  succ_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL DEFAULT 'FS'
);

CREATE TABLE IF NOT EXISTS risks (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  code TEXT NOT NULL,
  title_key TEXT,
  title_text TEXT NOT NULL DEFAULT '',
  mitigation_key TEXT,
  mitigation_text TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'technical',
  probability INTEGER NOT NULL,
  impact INTEGER NOT NULL,
  owner_id INTEGER REFERENCES members(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS budget_lines (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  category TEXT NOT NULL,
  label_text TEXT NOT NULL DEFAULT '',
  planned_minor INTEGER NOT NULL DEFAULT 0,
  actual_minor INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id INTEGER,
  at TEXT NOT NULL,
  kind TEXT NOT NULL,
  task_id INTEGER,
  old_value TEXT,
  new_value TEXT
);

CREATE TABLE IF NOT EXISTS content_translations (
  id INTEGER PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id INTEGER NOT NULL,
  field TEXT NOT NULL,
  locale TEXT NOT NULL,
  text TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'machine',
  translated_by TEXT NOT NULL DEFAULT 'machine',
  reviewed_by INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(entity_type, entity_id, field, locale)
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,            -- 'doc' or 'report'
  doc_type TEXT NOT NULL,
  locale TEXT NOT NULL,
  version INTEGER NOT NULL,
  engine TEXT NOT NULL DEFAULT 'template',
  content_json TEXT NOT NULL,
  share_token TEXT UNIQUE,
  created_by INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  user_id INTEGER,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  locale TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS missing_translations (
  key TEXT NOT NULL,
  locale TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (key, locale)
);

CREATE INDEX IF NOT EXISTS ix_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS ix_activity_project ON activity(project_id, at);
CREATE INDEX IF NOT EXISTS ix_docs_project ON documents(project_id, kind, doc_type);
"""

SCHEMA_VERSION = 1
_local = threading.local()


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def today() -> dt.date:
    return dt.date.today()


def connect() -> sqlite3.Connection:
    con = getattr(_local, "con", None)
    if con is None or getattr(_local, "path", None) != str(DB_PATH):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA journal_mode = WAL")
        _local.con, _local.path = con, str(DB_PATH)
    return con


def init():
    con = connect()
    con.executescript(SCHEMA)
    row = con.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        con.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
    con.commit()


def q(sql: str, args=()) -> list[sqlite3.Row]:
    return connect().execute(sql, args).fetchall()


def q1(sql: str, args=()):
    return connect().execute(sql, args).fetchone()


def ex(sql: str, args=()) -> int:
    con = connect()
    cur = con.execute(sql, args)
    con.commit()
    return cur.lastrowid


def exmany(sql: str, rows) -> None:
    con = connect()
    con.executemany(sql, rows)
    con.commit()


def set_path(path):
    """Used by tests."""
    global DB_PATH
    DB_PATH = Path(path)
    _local.con = None
