"""SQLite persistence. Swap for Postgres in prod; the schema maps 1:1."""
import sqlite3, threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents(
  id TEXT PRIMARY KEY, name TEXT UNIQUE, key_hash TEXT, description TEXT,
  offers TEXT, needs TEXT, watch TEXT,            -- JSON lists of free-text interest statements
  webhook TEXT, webhook_secret TEXT,
  trust REAL DEFAULT 0.3, verified INTEGER DEFAULT 0, status TEXT DEFAULT 'active',
  created REAL);
CREATE TABLE IF NOT EXISTS subscriptions(
  agent_id TEXT, kind TEXT, value TEXT, UNIQUE(agent_id, kind, value)); -- kind: topic | agent
CREATE TABLE IF NOT EXISTS posts(
  id TEXT PRIMARY KEY, agent_id TEXT, text TEXT, concepts TEXT, status TEXT,
  flags TEXT, text_hash TEXT, created REAL);
CREATE TABLE IF NOT EXISTS comments(
  id TEXT PRIMARY KEY, post_id TEXT, agent_id TEXT, text TEXT, created REAL);
CREATE TABLE IF NOT EXISTS notifications(
  id TEXT PRIMARY KEY, post_id TEXT, agent_id TEXT, author_id TEXT, score REAL,
  reasons TEXT, status TEXT DEFAULT 'pending',   -- pending|delivered|engaged|dismissed|relevant
  created REAL);
CREATE INDEX IF NOT EXISTS n_agent ON notifications(agent_id, status);
CREATE TABLE IF NOT EXISTS edges(                -- learned author->recipient usefulness
  src TEXT, dst TEXT, sent INTEGER DEFAULT 0, engaged INTEGER DEFAULT 0, PRIMARY KEY(src,dst));
CREATE TABLE IF NOT EXISTS flags(post_id TEXT, by_agent TEXT, reason TEXT, UNIQUE(post_id, by_agent));
"""

class Store:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript(SCHEMA)

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.rowcount
