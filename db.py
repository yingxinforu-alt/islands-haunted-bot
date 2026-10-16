"""SQLite storage for the ISLANDS Halloween Discord Bot."""
from __future__ import annotations

import datetime
import os
import sqlite3

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_id   TEXT PRIMARY KEY,
  display   TEXT,
  joined_at TEXT NOT NULL
);

-- Every scoring action is a ledger row. Personal/community totals are SUMs.
CREATE TABLE IF NOT EXISTS events (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id          TEXT NOT NULL,
  kind             TEXT NOT NULL,
  points           INTEGER NOT NULL,
  community_points INTEGER NOT NULL DEFAULT 0,
  counts_personal  INTEGER NOT NULL DEFAULT 1,
  day              TEXT NOT NULL,
  ref              TEXT,
  created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_events_day  ON events(day);

-- check-in and scoring UGC are once per day. Votes have their own table/cap.
DROP INDEX IF EXISTS uq_daily;
CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_core
  ON events(user_id, kind, day)
  WHERE kind IN ('checkin','ugc');

CREATE UNIQUE INDEX IF NOT EXISTS uq_milestone
  ON events(user_id, ref) WHERE kind='milestone';
CREATE UNIQUE INDEX IF NOT EXISTS uq_first
  ON events(user_id, ref) WHERE kind='first';
CREATE UNIQUE INDEX IF NOT EXISTS uq_participation
  ON events(user_id, ref) WHERE kind='participation';
CREATE UNIQUE INDEX IF NOT EXISTS uq_once_bonus
  ON events(user_id, kind) WHERE kind IN ('first_haunt','explorer');
CREATE UNIQUE INDEX IF NOT EXISTS uq_ugc_vote_bonus
  ON events(user_id, ref) WHERE kind='ugc_vote_bonus';

CREATE TABLE IF NOT EXISTS ugc_posts (
  thread_id        TEXT PRIMARY KEY,
  forum_id         TEXT NOT NULL,
  author_id        TEXT NOT NULL,
  day              TEXT NOT NULL,
  registered_at    TEXT NOT NULL,
  active           INTEGER NOT NULL DEFAULT 1,
  scoring_eligible INTEGER NOT NULL DEFAULT 1,
  vote_message_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_ugc_author ON ugc_posts(author_id);

CREATE TABLE IF NOT EXISTS ugc_votes (
  thread_id TEXT NOT NULL,
  voter_id  TEXT NOT NULL,
  day       TEXT NOT NULL,
  scored    INTEGER NOT NULL DEFAULT 0,
  voted_at  TEXT NOT NULL,
  PRIMARY KEY (thread_id, voter_id),
  FOREIGN KEY(thread_id) REFERENCES ugc_posts(thread_id)
);
CREATE INDEX IF NOT EXISTS idx_ugc_votes_voter_day ON ugc_votes(voter_id, day);

CREATE TABLE IF NOT EXISTS quiz_answers (
  quiz_id     TEXT NOT NULL,
  user_id     TEXT NOT NULL,
  choice      TEXT NOT NULL,
  correct     INTEGER NOT NULL,
  answered_at TEXT NOT NULL,
  PRIMARY KEY (quiz_id, user_id)
);

CREATE TABLE IF NOT EXISTS quiz_posted (
  quiz_id        TEXT PRIMARY KEY,
  message_id     TEXT,
  channel_id     TEXT,
  posted_at      TEXT NOT NULL,
  closes_at      TEXT,
  revealed_at    TEXT,
  correct_choice TEXT,
  question_text  TEXT,
  options_json   TEXT
);

CREATE TABLE IF NOT EXISTS community_nodes (
  node_idx    INTEGER PRIMARY KEY,
  unlocked_at TEXT NOT NULL
);
"""


def today_et() -> str:
    return datetime.datetime.now(config.TZ).strftime("%Y-%m-%d")


def now_dt() -> datetime.datetime:
    return datetime.datetime.now(config.TZ)


def now_iso() -> str:
    return now_dt().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    folder = os.path.dirname(config.DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _ensure_column(con: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init() -> sqlite3.Connection:
    con = connect()
    # Create missing tables first. On an older DB, CREATE IF NOT EXISTS keeps the old shape.
    con.executescript(SCHEMA)
    # Then add columns that did not exist in the previous Claude version.
    _ensure_column(con, "events", "community_points", "community_points INTEGER NOT NULL DEFAULT 0")
    _ensure_column(con, "events", "counts_personal", "counts_personal INTEGER NOT NULL DEFAULT 1")
    _ensure_column(con, "quiz_posted", "channel_id", "channel_id TEXT")
    _ensure_column(con, "quiz_posted", "closes_at", "closes_at TEXT")
    _ensure_column(con, "quiz_posted", "correct_choice", "correct_choice TEXT")
    _ensure_column(con, "quiz_posted", "question_text", "question_text TEXT")
    _ensure_column(con, "quiz_posted", "options_json", "options_json TEXT")
    # Neutralize legacy per-task "first" bonuses from the previous build if an old DB is reused.
    # Participation rows are metadata only and must never count toward personal ranking.
    con.execute("UPDATE events SET counts_personal=0 WHERE kind IN ('first','participation')")
    con.commit()
    return con
