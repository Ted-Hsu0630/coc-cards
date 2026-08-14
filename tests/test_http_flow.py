"""走真實 HTTP 路徑的整合測試。

單元測試直接呼叫 service，繞過了 cookie、相依注入與交易邊界。
這裡補上那一段 —— 特別是「請求失敗時 db.session 有沒有真的 rollback」，
那是相依注入的行為，不看實際請求就只能用猜的。
"""

import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    import config

    monkeypatch.setattr(config, "DB_PATH", pathlib.Path(tempfile.mkdtemp()) / "t.db")
    monkeypatch.setattr(config, "COOKIE_SECURE", False)

    from services import coc

    async def verify_token(tag, token):
        return token == "goodtok"

    async def get_player(tag):
        # 注意不要用含 O 的標籤當測試值：normalize 會把 O 轉成 0（見 core/tags.py）
        if tag == "#ZZZ":
            raise coc.PlayerNotFound("查無此標籤")
        return {"tag": tag, "name": f"村莊{tag}", "clan_tag": "#C1", "clan_name": "天堂"}

    async def noop():
        return None

    monkeypatch.setattr(coc, "verify_token", verify_token)
    monkeypatch.setattr(coc, "get_player", get_player)
    monkeypatch.setattr(coc, "startup", noop)
    monkeypatch.setattr(coc, "shutdown", noop)

    from app_factory import create_app

    with TestClient(create_app()) as c:
        yield c


def login(client, tag, token="goodtok"):
    return client.post("/api/players/verify", json={"tag": tag, "token": token})


def test_登入後拿得到_session_與村莊清單(client):
    assert login(client, "#MAIN").status_code == 200
    me = client.get("/api/me").json()
    assert me["logged_in"] is True
    assert me["active_tag"] == "#MAIN"
    assert [p["tag"] for p in me["players"]] == ["#MAIN"]


def test_加綁小號後兩個村莊都在同一個帳號(client):
    login(client, "#MAIN")
    assert login(client, "#ALT").status_code == 200
    me = client.get("/api/me").json()
    assert {p["tag"] for p in me["players"]} == {"#MAIN", "#ALT"}
    assert me["active_tag"] == "#ALT"


def test_清空_cookie_後用小號登入會回到同一個帳號(client):
    login(client, "#MAIN")
    login(client, "#ALT")
    client.cookies.clear()

    assert login(client, "#ALT").status_code == 200
    me = client.get("/api/me").json()
    assert {p["tag"] for p in me["players"]} == {"#MAIN", "#ALT"}


def test_被拒絕的加綁不留下孤兒帳號(client):
    """409 之後 users 表不可以多出一列 —— 驗證相依的 rollback 真的有作用。

    用「來源帳號有多個村莊」這個仍然會被拒的情境來測；
    單一村莊的帳號現在會直接併過來（見 test_account_merge.py）。
    """
    login(client, "#BBB")
    login(client, "#CCC")  # 這個帳號有兩個村莊
    client.cookies.clear()
    login(client, "#MAIN")

    r = client.post("/api/players/verify", json={"tag": "#BBB", "token": "goodtok"})
    assert r.status_code == 409
    assert "解除綁定" in r.json()["detail"]

    import config
    from core import db

    with db.session() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2, config.DB_PATH


def test_未登入不能讀收藏(client):
    assert client.get("/api/collection").status_code == 401


def test_收藏存取與配對(client):
    login(client, "#MAIN")
    r = client.put("/api/collection", json={"counts": {"elixir-01": 3, "elixir-02": 0}})
    assert r.status_code == 200
    assert r.json()["counts"] == {"elixir-01": 3}  # 0 不落地

    client.cookies.clear()
    login(client, "#MATE")
    client.put("/api/collection", json={"counts": {"elixir-02": 2}})

    client.cookies.clear()
    login(client, "#MAIN")
    m = client.get("/api/matches?same_clan=1").json()
    assert m["collected"] == 1
    assert [x["tag"] for x in m["matches"]] == ["#MATE"]
    assert m["matches"][0]["kind"] == "mutual"


def test_未知卡片_id_會被拒絕(client):
    login(client, "#MAIN")
    r = client.put("/api/collection", json={"counts": {"不存在的卡": 1}})
    assert r.status_code == 400


def test_張數超出上限會被拒絕(client):
    login(client, "#MAIN")
    r = client.put("/api/collection", json={"counts": {"elixir-01": 99}})
    assert r.status_code == 400


def test_標籤打錯回404而不是401(client):
    r = client.post("/api/players/verify", json={"tag": "#ZZZ", "token": "badtok"})
    assert r.status_code == 404


def test_部落總覽預設只顯示同部落(client, monkeypatch):
    from services import coc

    async def get_player(tag):
        clan = "#OTHER" if tag == "#FAR" else "#C1"
        return {"tag": tag, "name": f"村莊{tag}", "clan_tag": clan, "clan_name": clan}

    monkeypatch.setattr(coc, "get_player", get_player)

    login(client, "#FAR")          # 別的部落
    client.cookies.clear()
    login(client, "#MATE")         # 同部落
    client.cookies.clear()
    login(client, "#MAIN")

    default = client.get("/api/clan/overview").json()
    assert {r["tag"] for r in default["players"]} == {"#MAIN", "#MATE"}

    everyone = client.get("/api/clan/overview?same_clan=0").json()
    assert {r["tag"] for r in everyone["players"]} == {"#MAIN", "#MATE", "#FAR"}


def test_無部落時總覽至少看得到自己(client, monkeypatch):
    from services import coc

    async def get_player(tag):
        return {"tag": tag, "name": f"村莊{tag}", "clan_tag": None, "clan_name": None}

    monkeypatch.setattr(coc, "get_player", get_player)
    login(client, "#SINGLE")
    rows = client.get("/api/clan/overview").json()["players"]
    assert [r["tag"] for r in rows] == ["#SINGLE"]
