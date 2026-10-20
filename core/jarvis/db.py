"""SQLite storage: structured records plus the full-text index over Markdown memory."""

from __future__ import annotations

import sqlite3

from . import paths

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    product TEXT,
    tags TEXT,
    provenance TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT,
    source TEXT,
    source_ref TEXT,
    supersedes TEXT,
    superseded_by TEXT,
    created TEXT,
    updated TEXT,
    mtime REAL,
    size INTEGER
);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    id UNINDEXED, title, tags, body, tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS products (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'idea',
    notes TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES tasks(id),
    title TEXT NOT NULL,
    description TEXT,
    agent TEXT,
    product TEXT,
    status TEXT NOT NULL DEFAULT 'todo',
    priority INTEGER NOT NULL DEFAULT 2,
    deliverable TEXT,
    summary TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    completed TEXT
);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    product TEXT,
    task_id INTEGER,
    decision TEXT NOT NULL,
    rationale TEXT,
    decided_by TEXT NOT NULL DEFAULT 'user'
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    task_id INTEGER,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT,
    amount REAL,
    currency TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    decided_ts TEXT,
    decision_note TEXT
);

CREATE TABLE IF NOT EXISTS tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    angle TEXT,
    budget_usd REAL,
    planned_days INTEGER,
    criteria TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    started TEXT,
    ended TEXT,
    verdict TEXT,
    lesson TEXT,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id INTEGER NOT NULL REFERENCES tests(id),
    day TEXT NOT NULL,
    spend REAL DEFAULT 0,
    impressions INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    add_to_cart INTEGER DEFAULT 0,
    checkouts INTEGER DEFAULT 0,
    purchases INTEGER DEFAULT 0,
    revenue REAL DEFAULT 0,
    source TEXT,
    recorded TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    service TEXT NOT NULL,
    amount_aud REAL NOT NULL,
    task_id INTEGER,
    note TEXT
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    agent TEXT,
    session_id TEXT,
    event TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    tool TEXT NOT NULL,
    summary TEXT,
    session_id TEXT
);

CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    digest TEXT NOT NULL,
    UNIQUE (session_id, digest)
);
CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts USING fts5(text, tokenize = 'porter unicode61');

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    paths.home().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(paths.db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
