"""配對規則測試（SPEC §2、§6）。

配對正確性是這個站的全部價值，所以規則的每個角落都要有測試釘住 ——
特別是那兩個不對稱點：
  * 發起方換入的卡必須是自己「完全沒有」的（count == 0）
  * 接收方換入沒有任何限制（已擁有也照收）
"""

from services import matching
from services.matching import INCOMING, MUTUAL, OUTGOING

E1, E2, E3, E4 = "elixir-01", "elixir-02", "elixir-03", "elixir-04"
D1, D2 = "dark-01", "dark-02"


def pair(mine, theirs):
    return matching.match_one(mine, theirs)


# --- 基本狀態判定 ------------------------------------------------------------


def test_只有一張不算多餘_不能送出():
    # 我有 1 張 E1（不能送），對方缺 E1。不構成交換。
    assert pair({E1: 1}, {E2: 2}) is None


def test_兩張才算多餘():
    res = pair({E1: 2}, {E2: 2})
    assert res is not None
    kind, series, _, _ = res
    assert kind == MUTUAL
    assert series[0].i_give == [E1]
    assert series[0].i_get == [E2]


def test_沒有任何卡的兩個人配不出東西():
    assert pair({}, {}) is None


# --- 互利互換 ---------------------------------------------------------------


def test_互利需要雙方各自有多餘且各自缺對方的():
    mine = {E1: 3}      # 我多 E1、缺 E2
    theirs = {E2: 2}    # 對方多 E2、缺 E1
    kind, series, gain, helped = pair(mine, theirs)
    assert kind == MUTUAL
    s = series[0]
    assert s.i_give == [E1]
    assert s.i_get == [E2]
    assert (gain, helped) == (1, 1)


def test_持有一張不算缺_所以不構成互利():
    # 我有 1 張 E2 → 不算缺 → 我沒補到空缺 → 只是單向幫對方
    mine = {E1: 3, E2: 1}
    theirs = {E2: 2}
    kind, _, gain, helped = pair(mine, theirs)
    assert kind == OUTGOING
    assert gain == 0


def test_對方已有我要送的卡就不是互利():
    # 我多 E1，但對方也有 E1（1 張，不缺）→ 對方沒補到空缺
    mine = {E1: 2}
    theirs = {E1: 1, E2: 2}
    kind, series, _, _ = pair(mine, theirs)
    assert kind == INCOMING


# --- 單向：我受益 ------------------------------------------------------------


def test_我受益時我必須是發起方():
    mine = {E1: 2}
    theirs = {E1: 1, E2: 2}
    kind, _, _, _ = pair(mine, theirs)
    assert matching.INITIATOR[kind] == "me"


def test_我受益仍需付出同系列的多餘卡():
    # 我缺 E2、對方多 E2，但我在聖水系列一張多餘的都沒有 → 換不成
    mine = {E1: 1}
    theirs = {E2: 2}
    assert pair(mine, theirs) is None


def test_我受益時可送出對方已擁有的卡():
    # 接收方換入沒有限制，所以對方已有 3 張 E1 還是能收下我的 E1
    mine = {E1: 2}
    theirs = {E1: 3, E2: 2}
    kind, series, _, _ = pair(mine, theirs)
    assert kind == INCOMING
    assert E1 in series[0].i_give


# --- 單向：我幫人 ------------------------------------------------------------


def test_我幫人時對方是發起方():
    mine = {E1: 2, E2: 1}
    theirs = {E2: 2}  # 對方缺 E1、有多的 E2；我不缺 E2
    kind, series, _, _ = pair(mine, theirs)
    assert kind == OUTGOING
    assert matching.INITIATOR[kind] == "them"
    assert series[0].i_give == [E1]


def test_我幫人時對方也要拿得出同系列多餘卡():
    # 對方缺 E1、我有多的，但對方在聖水完全沒有多餘卡 → 換不成
    mine = {E1: 2}
    theirs = {E2: 1}
    assert pair(mine, theirs) is None


# --- 同系列限制（SPEC §2.3）--------------------------------------------------


def test_不可跨系列互換():
    # 我多聖水 E1、缺闇黑 D1；對方多 D1、缺 E1。跨系列 → 不成立
    mine = {E1: 2}
    theirs = {D1: 2}
    assert pair(mine, theirs) is None


def test_跨系列不成立但同系列成立時只回報同系列那組():
    mine = {E1: 2, D1: 2}
    theirs = {D1: 0, D2: 2, E1: 2}
    kind, series, _, _ = pair(mine, theirs)
    # 聖水：雙方都有 E1，沒人缺 → 無結果；闇黑：我多 D1 對方缺 D1，對方多 D2 我缺 D2
    assert [s.series for s in series] == ["dark"]
    assert kind == MUTUAL


def test_整體類型取最好的那個系列():
    # 聖水只能單向幫人，闇黑可以互利 → 整體算互利
    mine = {E1: 2, E2: 1, D1: 2}
    theirs = {E2: 2, D2: 2}
    kind, series, _, _ = pair(mine, theirs)
    assert kind == MUTUAL
    assert {s.series for s in series} == {"elixir", "dark"}


# --- find_matches 整體行為 ---------------------------------------------------


PLAYERS = {
    "#ME": {"name": "我", "clan_tag": "#CLAN", "clan_name": "天堂"},
    "#A": {"name": "阿明", "clan_tag": "#CLAN", "clan_name": "天堂"},
    "#B": {"name": "小華", "clan_tag": "#OTHER", "clan_name": "別團"},
    "#ALT": {"name": "我的小號", "clan_tag": "#CLAN", "clan_name": "天堂"},
}


def test_不會把自己列進配對結果():
    cols = {"#ME": {E1: 2, E2: 0}, "#A": {E2: 2}}
    out = matching.find_matches("#ME", cols, PLAYERS)
    assert all(m["tag"] != "#ME" for m in out)


def test_預設只顯示同部落():
    cols = {"#ME": {E1: 2}, "#B": {E2: 2, E1: 0}}
    assert matching.find_matches("#ME", cols, PLAYERS, same_clan_only=True) == []
    out = matching.find_matches("#ME", cols, PLAYERS, same_clan_only=False)
    assert [m["tag"] for m in out] == ["#B"]
    assert out[0]["same_clan"] is False


def test_自己的小號也會被列入配對():
    # 小號跟本尊同部落時一樣能互換，不該被排除
    cols = {"#ME": {E1: 2}, "#ALT": {E2: 2, E1: 0}}
    out = matching.find_matches("#ME", cols, PLAYERS)
    assert [m["tag"] for m in out] == ["#ALT"]
    assert out[0]["kind"] == MUTUAL


def test_互利排在單向前面():
    cols = {
        "#ME": {E1: 2, E2: 2},
        "#A": {E1: 5, E2: 5, E3: 2},          # 我受益（對方不缺我的）
        "#ALT": {E1: 0, E4: 2},               # 互利
    }
    out = matching.find_matches("#ME", cols, PLAYERS)
    assert [m["kind"] for m in out] == [MUTUAL, INCOMING]
    assert out[0]["tag"] == "#ALT"


def test_輸出含發起方與部落資訊():
    cols = {"#ME": {E1: 2}, "#A": {E2: 2, E1: 0}}
    out = matching.find_matches("#ME", cols, PLAYERS)
    m = out[0]
    assert m["initiator"] == "either"
    assert m["clan_name"] == "天堂"
    assert m["same_clan"] is True
    assert m["name"] == "阿明"


def test_沒有部落的人不會被誤判為同部落():
    players = dict(PLAYERS)
    players["#ME"] = {"name": "我", "clan_tag": None, "clan_name": None}
    cols = {"#ME": {E1: 2}, "#A": {E2: 2, E1: 0}}
    assert matching.find_matches("#ME", cols, players, same_clan_only=True) == []
