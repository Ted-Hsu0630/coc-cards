"""交換次數上限（SPEC §6.3）。

交換是一對一：從「送出候選」挑一張，換「收到候選」的一張。
把候選清單長度當成可換次數的話，介面會叫人一次送出三張只換回一張 ——
使用者會照做，然後白白浪費兩張卡。這個檔案就是釘住這件事。
"""

from services import matching
from services.matching import INCOMING, MUTUAL, OUTGOING

E = [f"elixir-{i:02d}" for i in range(1, 20)]
D = [f"dark-{i:02d}" for i in range(1, 14)]


def one(mine, theirs):
    return matching.match_one(mine, theirs)


def test_互利次數是兩邊候選數的較小者():
    # 我有 3 張多餘卡是對方缺的，對方只有 1 張多餘卡是我缺的
    mine = {E[2]: 2, E[3]: 2, E[6]: 2}
    theirs = {E[7]: 2}
    kind, series, gain, helped = one(mine, theirs)

    assert kind == MUTUAL
    s = series[0]
    assert len(s.i_give) == 3        # 候選有三個
    assert len(s.i_get) == 1
    assert s.trades == 1             # 但實際只能換一次
    assert (gain, helped) == (1, 1)  # 不是 (1, 3)


def test_互利次數對稱():
    mine = {E[0]: 2}
    theirs = {E[1]: 2, E[2]: 2, E[3]: 2}
    _, series, gain, helped = one(mine, theirs)
    assert series[0].trades == 1
    assert (gain, helped) == (1, 1)


def test_多個系列的次數會加總():
    mine = {E[0]: 2, E[1]: 2, D[0]: 2}
    theirs = {E[2]: 2, E[3]: 2, D[1]: 2}
    _, series, gain, helped = one(mine, theirs)
    assert {s.series: s.trades for s in series} == {"elixir": 2, "dark": 1}
    assert (gain, helped) == (3, 3)


def test_單向送出時同一張多備份可以換多次():
    # 我有 4 張聖水01（可送 3 張），對方有 3 張不同的我缺的卡
    mine = {E[0]: 4}
    theirs = {E[0]: 1, E[1]: 2, E[2]: 2, E[3]: 2}
    kind, series, gain, _ = one(mine, theirs)
    assert kind == INCOMING
    s = series[0]
    assert len(s.i_get) == 3
    assert s.trades == 3          # 3 張備份剛好換 3 次
    assert gain == 3


def test_單向送出的備份不夠時次數被壓低():
    # 同上但我只有 2 張（可送 1 張）
    mine = {E[0]: 2}
    theirs = {E[0]: 1, E[1]: 2, E[2]: 2, E[3]: 2}
    _, series, gain, _ = one(mine, theirs)
    s = series[0]
    assert len(s.i_get) == 3      # 候選還是 3 個
    assert s.trades == 1          # 但只換得起 1 次
    assert gain == 1


def test_我幫人時次數受對方張數限制():
    mine = {E[0]: 2, E[1]: 2, E[2]: 2, E[3]: 1}
    theirs = {E[3]: 2}            # 對方缺我那三張，但只有 1 張可送出的
    kind, series, gain, helped = one(mine, theirs)
    assert kind == OUTGOING
    s = series[0]
    assert len(s.i_give) == 3
    assert s.trades == 1
    assert (gain, helped) == (0, 1)


def test_送出候選按持有張數由多到少排序():
    """優先建議送出手上最多的那張，最不心疼。"""
    mine = {E[0]: 2, E[1]: 5, E[2]: 3}
    theirs = {E[7]: 2}
    _, series, _, _ = one(mine, theirs)
    assert series[0].i_give == [E[1], E[2], E[0]]   # 5 → 3 → 2


def test_候選清單一定夠配滿次數():
    """i_give/i_get 的長度必須 >= trades，否則介面配對時會出現空白格。

    實際踩過：單向交換的次數用「張數」算，但清單只列「種類」，
    我有 3 張同一張卡時 trades=2 而清單只有 1 個，第二列的送出欄變成空白。
    """
    import itertools
    import random

    ids = E + D
    rng = random.Random(7)
    for _ in range(300):
        mine = {c: rng.choice([0, 0, 1, 2, 3, 4]) for c in rng.sample(ids, 8)}
        theirs = {c: rng.choice([0, 0, 1, 2, 3, 4]) for c in rng.sample(ids, 8)}
        res = one(mine, theirs)
        if res is None:
            continue
        _, series, _, _ = res
        for s in series:
            assert s.trades >= 1, (s.series, s.kind)
            assert len(s.i_give) >= s.trades, (s.kind, s.i_give, s.trades)
            assert len(s.i_get) >= s.trades, (s.kind, s.i_get, s.trades)
            # 配出來的每一對都要是實際存在的卡
            # 兩邊長度可以不同（備選數不一樣），這裡只取前 trades 對
            for a, b in itertools.islice(zip(s.i_give, s.i_get, strict=False), s.trades):
                assert a and b


def test_單向送出同一張多份會重複列出():
    # 我有 4 張聖水01（可送 3 張），對方有 3 張不同的我缺的卡
    mine = {E[0]: 4}
    theirs = {E[0]: 1, E[1]: 2, E[2]: 2, E[3]: 2}
    _, series, _, _ = one(mine, theirs)
    s = series[0]
    assert s.trades == 3
    assert s.i_give[:3] == [E[0], E[0], E[0]]   # 三次都送同一張，各用掉一份


def test_對外輸出含次數欄位():
    players = {
        "#ME": {"name": "我", "clan_tag": "#C", "clan_name": "天堂"},
        "#A": {"name": "阿明", "clan_tag": "#C", "clan_name": "天堂"},
    }
    cols = {"#ME": {E[0]: 2, E[1]: 2, E[2]: 2}, "#A": {E[7]: 2}}
    out = matching.find_matches("#ME", cols, players)
    m = out[0]
    assert m["trades"] == 1
    assert m["gain"] == 1
    assert m["series"][0]["trades"] == 1
