"""「庫存最後更新時間」。

配對是拿別人存下來的資料在算，所以這個時間戳不只是裝飾 —— 它讓人看得出
算出來的建議是根據多舊的資料。釘住的重點是**它記的是誰做了什麼**，
不要跟 players.updated_at（部落同步整批寫入的）混在一起。
"""

import sqlite3

import pytest

from core import db
from services import players


def _login(client, tag="#AAA"):
    r = client.post("/api/players/verify", json={"tag": tag, "token": "goodtok"})
    assert r.status_code == 200


def test_存收藏才會更新時間戳(client):
    _login(client)
    before = client.get("/api/clan/overview?same_clan=0").json()["players"][0]
    assert before["collection_updated_at"] is None, "還沒存過就不該有時間"

    client.put("/api/collection", json={"counts": {"elixir-01": 2}})

    after = client.get("/api/clan/overview?same_clan=0").json()["players"][0]
    assert after["collection_updated_at"] is not None


def test_全部歸零也算一次更新(client):
    """save_collection 只寫 count > 0 的列，全部歸零時 collections 一列都不剩。

    時間戳要是掛在 collections 上就會跟著消失，別人看到的「最後更新」會倒退
    回更早的時間 —— 但「我把庫存清空」確實是一次真實的更新。
    """
    _login(client)
    client.put("/api/collection", json={"counts": {"elixir-01": 2}})
    first = client.get("/api/clan/overview?same_clan=0").json()["players"][0]

    client.put("/api/collection", json={"counts": {}})

    row = client.get("/api/clan/overview?same_clan=0").json()["players"][0]
    assert row["has_data"] is False, "確實清空了"
    assert row["collection_updated_at"] is not None, "清空的時間戳不見了"
    assert row["collection_updated_at"] >= first["collection_updated_at"]


def test_部落同步不會偽造成使用者更新(conn_with_player):
    """players.updated_at 由部落同步整批寫入，跟本人做了什麼無關。

    兩者混用的話，畫面上會顯示「大家今天都更新了」，而實際上那只是我們去
    CoC API 問過他們的資料 —— 這正是加這個欄位的原因。
    """
    conn, tag = conn_with_player
    players.save_collection(conn, tag, {"elixir-01": 1})
    saved = conn.execute(
        "SELECT collection_updated_at FROM players WHERE tag = ?", (tag,)
    ).fetchone()[0]

    # 模擬一次部落同步：只推進 updated_at
    conn.execute("UPDATE players SET updated_at = ? WHERE tag = ?", ("2099-01-01T00:00:00+00:00", tag))

    still = conn.execute(
        "SELECT collection_updated_at FROM players WHERE tag = ?", (tag,)
    ).fetchone()[0]
    assert still == saved, "部落同步動到了收藏的時間戳"


def test_舊資料庫升級後欄位存在且不被回填(tmp_path, monkeypatch):
    """`CREATE TABLE IF NOT EXISTS` 對既有的表不作用，所以一定要走 ALTER TABLE。

    而且**不可以回填**：既有玩家確實存過收藏，但我們不知道是什麼時候，
    拿 updated_at 或 verified_at 充數是在編造資料。畫面顯示「未知」才誠實。
    """
    import config

    path = tmp_path / "old.db"
    monkeypatch.setattr(config, "DB_PATH", path)

    # 先造一個沒有這個欄位的舊庫
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL);
        CREATE TABLE players (
          tag TEXT PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL,
          clan_tag TEXT, clan_name TEXT, clan_synced_at TEXT,
          verified_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0);
        INSERT INTO users (id, created_at) VALUES (1, '2026-01-01T00:00:00+00:00');
        INSERT INTO players (tag, user_id, name, verified_at, updated_at)
        VALUES ('#OLD', 1, '舊玩家', '2026-01-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00');
    """)
    old.commit()
    old.close()

    db.init()

    with db.session() as conn:
        # 用 PRAGMA 而不是 `in row.keys()`：sqlite3.Row 的 `in` 比對的是值不是
        # 欄位名，寫成那樣會安靜地測錯東西（ruff 的 SIM118 也會想把它「修」壞）。
        cols = {c["name"] for c in conn.execute("PRAGMA table_info(players)")}
        r = conn.execute("SELECT * FROM players WHERE tag = '#OLD'").fetchone()
    assert "collection_updated_at" in cols, "欄位沒有被加上去"
    assert r["collection_updated_at"] is None, "不該拿別的欄位回填"
    assert r["updated_at"] == "2026-08-01T00:00:00+00:00", "既有資料被動到了"


@pytest.fixture
def conn_with_player(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init()
    with db.session() as conn:
        uid = conn.execute("INSERT INTO users (created_at) VALUES (?)", (db.now(),)).lastrowid
        players.upsert_player(
            conn, "#AAA", uid,
            {"name": "阿明", "clan_tag": "#C1", "clan_name": "天堂"},
        )
        yield conn, "#AAA"
