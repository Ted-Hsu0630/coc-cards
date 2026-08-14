"""SQLite 存取。schema 見 SPEC §5。"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
  tag             TEXT PRIMARY KEY,
  user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  clan_tag        TEXT,
  clan_name       TEXT,
  clan_synced_at  TEXT,
  verified_at     TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_players_user ON players(user_id);
CREATE INDEX IF NOT EXISTS idx_players_clan ON players(clan_tag);

CREATE TABLE IF NOT EXISTS collections (
  tag        TEXT NOT NULL REFERENCES players(tag) ON DELETE CASCADE,
  card_id    TEXT NOT NULL,
  count      INTEGER NOT NULL CHECK (count >= 0),
  PRIMARY KEY (tag, card_id)
);

CREATE TABLE IF NOT EXISTS sessions (
  token       TEXT PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  active_tag  TEXT,
  expires_at  TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False 是必要的：FastAPI 把同步相依（get_conn）丟到 threadpool 執行，
    # 但 async 的路由函式跑在事件迴圈執行緒上，於是連線會在 A 執行緒建立、在 B 執行緒使用。
    # 這裡安全的前提是**一個連線只服務一個請求、且不並發使用** —— 由 db.session() 保證。
    # 不要改成模組層級的共用連線，那樣這個旗標就會變成真正的資料競爭。
    conn = sqlite3.connect(config.DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
