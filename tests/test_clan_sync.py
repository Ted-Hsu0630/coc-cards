"""部落同步：先查部落、再補查個人。

這裡釘的是**呼叫次數**，不是結果。結果對不對用逐人查也會對 —— 差別在於逐人查
的成本跟玩家數成正比（並發 10，正式機實測 17 人 886ms、50 人 2.2 秒，而且會
一直長），而按部落查是跟部落數成正比（正式機 2 個部落涵蓋 80 名成員）。

所以「有沒有退回逐人查」才是這段程式碼真正的價值，也是重構時最容易安靜失去
的東西 —— 全部改回逐人查，測試如果只看資料正確性會全部通過。
"""

import datetime as dt

import pytest

import config
from core import db
from services import coc, players


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init()
    with db.session() as c:
        yield c


def add_player(conn, tag, name, clan_tag, clan_name, *, synced_min_ago=999):
    """塞一個玩家。預設時間戳很舊，所以下一次同步一定會撿到他。"""
    uid = conn.execute("INSERT INTO users (created_at) VALUES (?)", (db.now(),)).lastrowid
    ts = (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=synced_min_ago)).isoformat()
    conn.execute(
        "INSERT INTO players (tag, user_id, name, clan_tag, clan_name, clan_synced_at,"
        " verified_at, updated_at, sort_order) VALUES (?,?,?,?,?,?,?,?,0)",
        (tag, uid, name, clan_tag, clan_name, ts, db.now(), db.now()),
    )


class Spy:
    """記下 coc 被打了幾次、打了誰。"""

    def __init__(self, monkeypatch, clans, players_=None):
        self.clan_calls, self.player_calls = [], []
        self._clans, self._players = clans, players_ or {}

        async def get_clans(tag_list):
            self.clan_calls.append(list(tag_list))
            return {t: self._clans.get(t) for t in tag_list}

        async def get_players(tag_list):
            self.player_calls.append(list(tag_list))
            return {t: self._players.get(t) for t in tag_list}

        monkeypatch.setattr(coc, "get_clans", get_clans)
        monkeypatch.setattr(coc, "get_players", get_players)

    @property
    def players_queried(self):
        return [t for call in self.player_calls for t in call]


def clan(tag, name, members):
    return {"tag": tag, "name": name, "members": [{"tag": t, "name": n} for t, n in members]}


async def test_部落名單涵蓋到的人完全不會被逐一查詢(conn, monkeypatch):
    """這條就是整個優化的意義。

    三個人同部落 —— 打一次部落就夠了，一次個人查詢都不該發生。
    退回逐人查的話 API 呼叫會從 1 次變 3 次，而畫面上完全看不出差別。
    """
    for tag, name in [("#A", "阿明"), ("#B", "小華"), ("#C", "老王")]:
        add_player(conn, tag, name, "#C1", "天堂")
    spy = Spy(monkeypatch, {"#C1": clan("#C1", "天堂", [("#A", "阿明"), ("#B", "小華"), ("#C", "老王")])})

    updated = await players.sync_clans(conn)

    assert updated == 3
    assert spy.clan_calls == [["#C1"]], "應該只打一次部落"
    assert spy.player_calls == [], "有人被逐一查詢了"


async def test_同一個部落只查一次(conn, monkeypatch):
    """十個人同部落就打十次的話，等於什麼都沒省到。"""
    for i in range(10):
        add_player(conn, f"#P{i}", f"玩家{i}", "#C1", "天堂")
    spy = Spy(monkeypatch, {"#C1": clan("#C1", "天堂", [(f"#P{i}", f"玩家{i}") for i in range(10)])})

    await players.sync_clans(conn)

    assert spy.clan_calls == [["#C1"]]
    assert spy.player_calls == []


async def test_離開部落的人才退回逐一查詢(conn, monkeypatch):
    """名單裡找不到 = 這個人不在我們以為的部落裡了。

    這時只能個別問他現在在哪 —— 但**只問他一個**，不是整批重來。
    """
    add_player(conn, "#A", "阿明", "#C1", "天堂")
    add_player(conn, "#GONE", "跑掉了", "#C1", "天堂")
    spy = Spy(
        monkeypatch,
        {"#C1": clan("#C1", "天堂", [("#A", "阿明")])},
        {"#GONE": {"name": "跑掉了", "clan_tag": "#C2", "clan_name": "別的部落"}},
    )

    await players.sync_clans(conn)

    assert spy.players_queried == ["#GONE"], "只有名單外的人該被個別查"
    row = conn.execute("SELECT clan_tag, clan_name FROM players WHERE tag='#GONE'").fetchone()
    assert (row["clan_tag"], row["clan_name"]) == ("#C2", "別的部落"), "沒換成新部落"


async def test_沒有部落的人直接個別查(conn, monkeypatch):
    """clan_tag 是 NULL 的人沒有部落名單可查，只能問本人。"""
    add_player(conn, "#SOLO", "獨行俠", None, None)
    spy = Spy(monkeypatch, {}, {"#SOLO": {"name": "獨行俠", "clan_tag": None, "clan_name": None}})

    await players.sync_clans(conn)

    assert spy.clan_calls == [], "沒有部落可查卻打了部落端點"
    assert spy.players_queried == ["#SOLO"]


async def test_某個部落查不到時那些人退回個別查(conn, monkeypatch):
    """部落解散或標籤失效時回 None。

    這時**不可以**讓那些人整批失去更新 —— 他們多半只是換了部落，個別問得到。
    而另一個正常的部落也不該被波及。
    """
    add_player(conn, "#A", "阿明", "#C1", "天堂")
    add_player(conn, "#B", "小華", "#DEAD", "解散了")
    spy = Spy(
        monkeypatch,
        {"#C1": clan("#C1", "天堂", [("#A", "阿明")]), "#DEAD": None},
        {"#B": {"name": "小華", "clan_tag": "#C1", "clan_name": "天堂"}},
    )

    await players.sync_clans(conn)

    assert spy.players_queried == ["#B"]
    row = conn.execute("SELECT clan_name FROM players WHERE tag='#B'").fetchone()
    assert row["clan_name"] == "天堂"


async def test_連個別查都查不到就只推進時間戳(conn, monkeypatch):
    """否則每一次配對都會重試同一個壞掉的 tag，永遠付這筆錢。"""
    add_player(conn, "#BAD", "查無此人", "#C1", "天堂")
    Spy(monkeypatch, {"#C1": clan("#C1", "天堂", [])}, {"#BAD": None})
    before = conn.execute("SELECT clan_synced_at FROM players WHERE tag='#BAD'").fetchone()[0]

    updated = await players.sync_clans(conn)

    after = conn.execute("SELECT clan_synced_at FROM players WHERE tag='#BAD'").fetchone()[0]
    assert updated == 0
    assert after > before, "時間戳沒推進，下次會再查一遍"


async def test_快取沒過期就一次都不打(conn, monkeypatch):
    """一般情況下開配對頁是零外部呼叫，這是快取存在的理由。"""
    add_player(conn, "#A", "阿明", "#C1", "天堂", synced_min_ago=0)
    spy = Spy(monkeypatch, {"#C1": clan("#C1", "天堂", [("#A", "阿明")])})

    assert await players.sync_clans(conn) == 0
    assert spy.clan_calls == [] and spy.player_calls == []
