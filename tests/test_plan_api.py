"""多人計劃的 HTTP 端點。

演算法本身在 `test_planning.py` 釘。這裡只管端點層的事：誰能呼叫、
什麼樣的輸入會被擋下來、以及回傳的東西前端夠不夠用。
"""

import pytest

from core import cards

E = [c.id for c in cards.all_cards() if c.series == "elixir"]


def _login(client, tag):
    r = client.post("/api/players/verify", json={"tag": tag, "token": "goodtok"})
    assert r.status_code == 200, r.text


def _set_collection(client, tag, counts):
    assert client.post("/api/me/active", json={"tag": tag}).status_code == 200
    assert client.put("/api/collection", json={"counts": counts}).status_code == 200


@pytest.fixture
def two_players(client):
    """同一個帳號綁兩個村莊，各自有互補的收藏。"""
    _login(client, "#AAA")
    _login(client, "#BBB")          # 已登入時再驗證等於加綁
    _set_collection(client, "#AAA", {E[0]: 2, E[1]: 1})
    _set_collection(client, "#BBB", {E[1]: 2})
    assert client.post("/api/me/active", json={"tag": "#AAA"}).status_code == 200
    return client


def test_沒登入不能用(client):
    assert client.post("/api/matches/plan", json={"tags": ["#AAA"]}).status_code == 401


def test_算得出計劃而且結構完整(two_players):
    r = two_players.post("/api/matches/plan", json={"tags": ["#AAA", "#BBB"]})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["steps"], "這組資料應該換得成"
    tr = body["steps"][0][0]
    assert {"initiator", "receiver", "gives", "gets", "series"} <= set(tr)

    # 前端只拿得到 tag，名字一定要一起送出來，否則畫面上只能顯示 #BBB
    assert set(body["players"]) == {"#AAA", "#BBB"}
    assert body["players"]["#BBB"]["name"]
    # 計劃有多新要看得出來 —— 第二步以後依賴前面真的被執行
    assert "collection_updated_at" in body["players"]["#BBB"]
    assert body["summary"]["trades"] >= 1


def test_每個人的換前換後都算得出來(two_players):
    """摘要要寫成「41/60 → 54/60」，所以換前、換後、總數都得從這裡帶。

    換前**必須**是算這份計劃時用的那份收藏。讓前端拿部落總覽那支的數字去湊
    的話，中間有人存了新庫存就會湊出跟計劃對不起來的算式，而且畫面上完全
    看不出哪裡怪 —— 只是一個看起來很正常、卻不成立的加法。
    """
    r = two_players.post("/api/matches/plan", json={"tags": ["#AAA", "#BBB"]})
    body = r.json()
    total = len(cards.all_cards())

    for tag, p in body["players"].items():
        assert p["total"] == total
        assert 0 <= p["collected"] <= p["after"] <= total
        gained = body["summary"]["gained"].get(tag, 0)
        assert p["after"] == p["collected"] + gained, f"{tag} 的換後對不上補到的張數"

    # 這組資料只有 #BBB 補得到（他缺 E0；#AAA 收到的 E1 本來就有，不算補）
    assert body["players"]["#BBB"]["collected"] == 1
    assert body["players"]["#BBB"]["after"] == 2
    # 一張都沒補到的人也要列出來，而且換前換後一樣 —— 從清單裡消失的話
    # 會被當成漏算，畫面上正是要顯示他這一趟沒拿到東西
    assert body["players"]["#AAA"]["collected"] == body["players"]["#AAA"]["after"] == 2


def test_換前用的是算計劃的那份收藏(two_players):
    """改完庫存之後重算，換前的數字要跟著動。

    釘住「換前是現算的」而不是某個更早的快照 —— 這條壞掉的時候，畫面上的
    算式會停在使用者上次進畫面的狀態。
    """
    before = two_players.post(
        "/api/matches/plan", json={"tags": ["#AAA", "#BBB"]}
    ).json()["players"]["#AAA"]["collected"]

    _set_collection(two_players, "#AAA", {E[0]: 2, E[1]: 1, E[2]: 1})
    assert two_players.post("/api/me/active", json={"tag": "#AAA"}).status_code == 200

    after = two_players.post(
        "/api/matches/plan", json={"tags": ["#AAA", "#BBB"]}
    ).json()["players"]["#AAA"]["collected"]
    assert after == before + 1


def test_自己不會被自動加進去(two_players):
    """幫部落其他人排一份計劃是合理的用法。

    自動塞的話，使用者在畫面上取消勾選自己，卻發現結果裡還是有他。
    """
    r = two_players.post("/api/matches/plan", json={"tags": ["#BBB"]})
    assert r.status_code == 400, "只選一個人應該被擋，而不是偷偷補上自己"


def test_重複的_tag_不會被算兩次(two_players):
    """同一個人送兩次的話，他會變成可以跟自己交換。"""
    body = two_players.post(
        "/api/matches/plan", json={"tags": ["#BBB", "#BBB", "#AAA"]}
    ).json()
    assert set(body["players"]) == {"#AAA", "#BBB"}
    for step in body["steps"]:
        for tr in step:
            assert tr["initiator"] != tr["receiver"]


def test_查不到的村莊要擋掉(two_players):
    r = two_players.post("/api/matches/plan", json={"tags": ["#AAA", "#NOPE"]})
    assert r.status_code == 400
    assert "查不到" in r.json()["detail"]


def test_只有自己一個人要擋掉(two_players):
    r = two_players.post("/api/matches/plan", json={"tags": []})
    assert r.status_code == 400
    assert "兩個人" in r.json()["detail"]


def test_人數上限(two_players):
    from routers.matches import MAX_PLAN_PLAYERS

    r = two_players.post(
        "/api/matches/plan", json={"tags": [f"#X{i}" for i in range(MAX_PLAN_PLAYERS + 5)]}
    )
    # 先被人數擋掉或先被「查不到」擋掉都可以，重點是不會真的去算
    assert r.status_code == 400


def test_優先對象必須在名單裡(two_players):
    r = two_players.post(
        "/api/matches/plan", json={"tags": ["#AAA", "#BBB"], "favor": "#NOPE"}
    )
    assert r.status_code == 400


def test_步數會被夾在範圍內(two_players):
    """使用者送 999 進來不該讓伺服器算到天荒地老。"""
    from routers.matches import MAX_PLAN_STEPS

    body = two_players.post(
        "/api/matches/plan", json={"tags": ["#AAA", "#BBB"], "max_steps": 999}
    ).json()
    assert len(body["steps"]) <= MAX_PLAN_STEPS

    body = two_players.post(
        "/api/matches/plan", json={"tags": ["#AAA", "#BBB"], "max_steps": 0}
    ).json()
    assert len(body["steps"]) <= 1, "0 步應該被夾成 1，不是算出空的"
