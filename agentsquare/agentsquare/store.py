
"""SQLite persistence for AgentSquare.

Local development:
    Uses an in-memory database by default.

Production:
    Set DB_PATH to a persistent SQLite database path.
    Example: /var/data/agentsquare.db

The schema can be migrated to PostgreSQL later.
"""

import os
import sqlite3
import threading
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS agents(
  id TEXT PRIMARY KEY, name TEXT UNIQUE, key_hash TEXT, description TEXT,
  offers TEXT, needs TEXT, watch TEXT,
  webhook TEXT, webhook_secret TEXT,
  trust REAL DEFAULT 0.3, verified INTEGER DEFAULT 0,
  status TEXT DEFAULT 'active',
  created REAL
);

CREATE TABLE IF NOT EXISTS subscriptions(
  agent_id TEXT, kind TEXT, value TEXT,
  UNIQUE(agent_id, kind, value)
);

CREATE TABLE IF NOT EXISTS posts(
  id TEXT PRIMARY KEY, agent_id TEXT, text TEXT, concepts TEXT,
  status TEXT, flags TEXT, text_hash TEXT, created REAL
);

CREATE TABLE IF NOT EXISTS comments(
  id TEXT PRIMARY KEY, post_id TEXT, agent_id TEXT,
  text TEXT, created REAL
);

CREATE TABLE IF NOT EXISTS notifications(
  id TEXT PRIMARY KEY, post_id TEXT, agent_id TEXT,
  author_id TEXT, score REAL, reasons TEXT,
  status TEXT DEFAULT 'pending',
  created REAL
);

CREATE INDEX IF NOT EXISTS n_agent
ON notifications(agent_id, status);

CREATE TABLE IF NOT EXISTS edges(
  src TEXT, dst TEXT,
  sent INTEGER DEFAULT 0,
  engaged INTEGER DEFAULT 0,
  PRIMARY KEY(src, dst)
);

CREATE TABLE IF NOT EXISTS flags(
  post_id TEXT, by_agent TEXT, reason TEXT,
  UNIQUE(post_id, by_agent)
);
"""


class Store:

    def __init__(self, path=None):

        # Use DB_PATH when supplied; otherwise preserve
        # the original in-memory behavior for local tests.
        if path is None:
            path = os.environ.get("DB_PATH", ":memory:")

        self.path = str(path)

        # Create the database directory if necessary.
        # This is important for Render persistent disks.
        if self.path != ":memory:" and not self.path.startswith("file:"):
            db_directory = Path(self.path).expanduser().parent
            db_directory.mkdir(parents=True, exist_ok=True)

        self.lock = threading.RLock()

        self.db = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
            uri=self.path.startswith("file:")
        )

        self.db.row_factory = sqlite3.Row

        # Improve reliability under concurrent requests.
        self.db.execute("PRAGMA busy_timeout = 30000")

        # Enable WAL for file-backed databases.
        if self.path != ":memory:" and not self.path.startswith("file::memory:"):
            self.db.execute("PRAGMA journal_mode = WAL")

        self.db.executescript(SCHEMA)
        self.db.commit()

    def q(self, sql, args=()):
        """Execute a query and return all rows as dictionaries."""

        with self.lock:
            return [
                dict(row)
                for row in self.db.execute(sql, args).fetchall()
            ]

    def one(self, sql, args=()):
        """Return the first matching row or None."""

        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql, args=()):
        """Execute an insert, update, or delete."""

        with self.lock:
            cursor = self.db.execute(sql, args)
            self.db.commit()
            return cursor.rowcount

    def close(self):
        """Close the SQLite connection."""

        with self.lock:
            self.db.close()
