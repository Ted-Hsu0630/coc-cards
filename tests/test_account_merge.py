"""把先各自單獨登入的帳號合併回來。

實際情境：成員不知道有加綁小號的功能，兩隻帳號各自登入，於是產生兩個獨立帳號。
之後想加綁就一直看到「已被綁定」，兩邊永遠合不起來。
"""

import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

import config


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", pathlib.Path(tempfile.mkdtemp()) / "t.db")
    monkeypatch.setattr(config, "COOKIE_SECURE", False)

    from services import coc

    async def verify_token(tag, token):
        return token == "goodtok"

    async def get_player(tag):
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


def login(client, tag):
    return client.post("/api/players/verify", json={"tag": tag, "token": "goodtok"})


def users_count():
    from core import db

    with db.session() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def test_兩隻各自登入後可以合併(client):
    login(client, "#AAA")
    client.cookies.clear()
    login(client, "#BBB")            # 各自建立了獨立帳號
    assert users_count() == 2

    client.cookies.clear()
    login(client, "#AAA")
    r = login(client, "#BBB")        # 已登入 A，加綁 B
    assert r.status_code == 200
    assert r.json()["migrated"] is True

    me = client.get("/api/me").json()
    assert {p["tag"] for p in me["players"]} == {"#AAA", "#BBB"}
    assert users_count() == 1        # 空掉的來源帳號被清掉


def test_合併不會弄丟收藏(client):
    login(client, "#AAA")
    client.put("/api/collection", json={"counts": {"elixir-01": 3}})

    client.cookies.clear()
    login(client, "#BBB")
    client.put("/api/collection", json={"counts": {"dark-05": 2, "super-02": 4}})

    client.cookies.clear()
    login(client, "#AAA")
    login(client, "#BBB")            # 合併

    assert client.get("/api/collection").json()["counts"] == {"dark-05": 2, "super-02": 4}
    client.post("/api/me/active", json={"tag": "#AAA"})
    assert client.get("/api/collection").json()["counts"] == {"elixir-01": 3}


def test_一般加綁不算搬移(client):
    login(client, "#AAA")
    r = login(client, "#BBB")        # 全新的村莊，不是從別的帳號搬來的
    assert r.json()["migrated"] is False
    assert users_count() == 1


def test_來源帳號有多個村莊時拒絕並說明怎麼辦(client):
    login(client, "#BBB")
    login(client, "#CCC")            # 這個帳號有兩個村莊
    client.cookies.clear()
    login(client, "#AAA")

    r = client.post("/api/players/verify", json={"tag": "#BBB", "token": "goodtok"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "村莊#CCC" in detail       # 有講出是被哪個村莊卡住
    assert "解除綁定" in detail       # 有講出該怎麼辦
    assert users_count() == 2        # 什麼都沒被動到


def test_另一台裝置解除綁定後舊_session_會自動退回可用的村莊(client):
    """實際會發生：同一帳號在另一台裝置解除綁定，舊裝置的 active_tag 就懸空了。

    修正前的症狀很嚇人 —— 使用者看到一張空白的 60 格收藏表（以為資料不見了），
    重填一次按儲存還會被 403 擋掉。
    """
    login(client, "#AAA")
    login(client, "#BBB")               # 同帳號兩個村莊，目前選的是 #BBB
    old_device = dict(client.cookies)

    client.cookies.clear()              # 換一台裝置
    login(client, "#AAA")
    client.delete("/api/players/%23BBB")

    client.cookies.clear()              # 回到舊裝置
    for k, v in old_device.items():
        client.cookies.set(k, v)

    me = client.get("/api/me").json()
    assert me["active_tag"] == "#AAA"   # 自動退回仍持有的村莊，不是懸空的 #BBB
    assert client.get("/api/collection").json()["tag"] == "#AAA"
    assert client.put("/api/collection", json={"counts": {"elixir-01": 2}}).status_code == 200


def test_一個村莊都不剩時給明確錯誤(client):
    login(client, "#AAA")
    client.delete("/api/players/%23AAA")
    r = client.get("/api/collection")
    assert r.status_code == 400
    assert "村莊" in r.json()["detail"]


def test_搬移後原帳號的_session_失效(client):
    login(client, "#BBB")
    stolen = dict(client.cookies)    # #BBB 原本那台裝置的 session

    client.cookies.clear()
    login(client, "#AAA")
    login(client, "#BBB")            # 合併，來源帳號被刪

    client.cookies.clear()
    for k, v in stolen.items():
        client.cookies.set(k, v)
    assert client.get("/api/me").json()["logged_in"] is False
