"""舊資料庫的欄位遷移。

`CREATE TABLE IF NOT EXISTS` 對已存在的表完全不作用，所以新增欄位一定要走
ALTER TABLE。這件事在本機看不出來（本機資料庫都是新建的），只有正式環境會炸，
所以用「先建舊 schema 再跑 init()」的方式明確測。
"""

import pathlib
import sqlite3
import tempfile

import pytest

import config

OLD_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL);
CREATE TABLE players (
  tag TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL, clan_tag TEXT, clan_name TEXT, clan_synced_at TEXT,
  verified_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE collections (
  tag TEXT NOT NULL REFERENCES players(tag) ON DELETE CASCADE,
  card_id TEXT NOT NULL, count INTEGER NOT NULL CHECK (count >= 0),
  PRIMARY KEY (tag, card_id));
CREATE TABLE sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  active_tag TEXT, expires_at TEXT NOT NULL, created_at TEXT NOT NULL);
"""


@pytest.fixture
def old_db(monkeypatch):
    path = pathlib.Path(tempfile.mkdtemp()) / "old.db"
    monkeypatch.setattr(config, "DB_PATH", path)

    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    for uid in (1, 2):
        conn.execute("INSERT INTO users (id, created_at) VALUES (?, ?)", (uid, "2026-08-01"))
    # 刻意讓 tag 的字母序與 verified_at 順序不一致，才驗得出回填是照時間走的
    for tag, uid, when in [
        ("#A1", 1, "2026-08-03"),
        ("#A2", 1, "2026-08-01"),
        ("#A3", 1, "2026-08-02"),
        ("#B1", 2, "2026-08-05"),
    ]:
        conn.execute(
            "INSERT INTO players (tag,user_id,name,verified_at,updated_at) VALUES (?,?,?,?,?)",
            (tag, uid, f"村莊{tag}", when, when),
        )
    conn.execute("INSERT INTO collections VALUES ('#A1','elixir-01',3)")
    conn.commit()
    conn.close()
    return path


def test_舊資料庫會補上_sort_order_欄位(old_db):
    from core import db
    from services import players

    db.init()
    with db.session() as conn:
        mine = players.players_of_user(conn, 1)
        assert [p["tag"] for p in mine] == ["#A2", "#A3", "#A1"]   # 照 verified_at 回填
        assert [p["sort_order"] for p in mine] == [0, 1, 2]


def test_每個帳號各自從零開始編號(old_db):
    from core import db
    from services import players

    db.init()
    with db.session() as conn:
        assert [p["sort_order"] for p in players.players_of_user(conn, 2)] == [0]


def test_遷移不會弄丟收藏(old_db):
    from core import db
    from services import players

    db.init()
    with db.session() as conn:
        assert players.get_collection(conn, "#A1") == {"elixir-01": 3}


def test_遷移是冪等的(old_db):
    from core import db
    from services import players

    db.init()
    with db.session() as conn:
        players.set_order(conn, 1, ["#A1", "#A2", "#A3"])

    db.init()   # 再跑一次不可以把使用者調好的順序洗掉
    with db.session() as conn:
        assert [p["tag"] for p in players.players_of_user(conn, 1)] == ["#A1", "#A2", "#A3"]
