"""多人換卡計劃。

最重要的是**第一個**測試：把算出來的計劃一步一步套用回去，每一筆都必須在
當下的手牌狀態下合法。它抓到的第一個 bug 就是「只併入收到的、忘了扣掉送出的」
—— 第二步讓同一個人又送出一張他第一步已經送掉的卡，而畫面上完全看不出來，
使用者要到遊戲裡按下去才發現換不成。

規則本身在 matching.py 開頭（SPEC §2.2）：發起方送出要 >= 2 張、收到的必須
是自己完全沒有的；接收方送出一樣要 >= 2 張、收到則無限制；只能同系列。
"""

import random

import pytest

from core import cards
from services import planning

SERIES = {c.id: c.series for c in cards.all_cards()}
BY_SERIES: dict[str, list[str]] = {}
for _c in cards.all_cards():
    BY_SERIES.setdefault(_c.series, []).append(_c.id)

E = BY_SERIES["elixir"]      # 同系列才能換，測試資料一律用聖水
D = BY_SERIES["dark"]


def replay(coll, steps):
    """逐步套用計劃，順便驗證每一筆的合法性。回傳最終手牌。

    這就是使用者拿著清單在遊戲裡照做會發生的事。跑得完 = 計劃可執行。
    """
    have = {t: dict(c) for t, c in coll.items()}
    for i, batch in enumerate(steps, 1):
        start = {t: dict(c) for t, c in have.items()}   # 這一步開始時的狀態
        avail = {t: dict(c) for t, c in have.items()}   # 邊做邊扣
        incoming: dict[str, dict[str, int]] = {t: {} for t in have}

        for tr in batch:
            ini, rec, gives, gets = tr["initiator"], tr["receiver"], tr["gives"], tr["gets"]
            where = f"第 {i} 步 {ini} 送 {gives} 換 {rec} 的 {gets}"

            assert SERIES[gives] == SERIES[gets], f"{where}：跨系列交換"
            assert avail[ini].get(gives, 0) >= 2, f"{where}：發起方沒有多的可以送"
            assert avail[rec].get(gets, 0) >= 2, f"{where}：接收方沒有多的可以送"
            # 紅線 2 —— 用「這一步開始時」的狀態判斷：同一步的交換是同時發生的
            assert start[ini].get(gets, 0) == 0, f"{where}：發起方本來就有這張卡"
            assert gets not in incoming[ini], f"{where}：同一步收到同一張卡兩次"

            avail[ini][gives] -= 1
            avail[rec][gets] -= 1
            incoming[rec][gives] = incoming[rec].get(gives, 0) + 1
            incoming[ini][gets] = incoming[ini].get(gets, 0) + 1

        have = avail
        for t, gained in incoming.items():
            for cid, n in gained.items():
                have[t][cid] = have[t].get(cid, 0) + n
    return have


# ── 可執行性 ───────────────────────────────────────────────────────


def _random_collections(n_players, seed):
    rng = random.Random(seed)
    out = {}
    for i in range(n_players):
        counts = {}
        for cid in E + D:
            r = rng.random()
            if r < 0.35:
                continue                      # 沒有
            counts[cid] = 1 if r < 0.75 else rng.randint(2, 4)
        out[f"#P{i}"] = counts
    return out


@pytest.mark.parametrize("seed", range(12))
def test_算出來的計劃一定跑得完(seed):
    """隨機資料下，每一筆交換都必須在當下狀態合法。

    這條是整個模組的安全網。手工造的案例只涵蓋想得到的情況，而這裡的 bug
    幾乎都長成「某個狀態沒有正確帶到下一步」—— 那種錯誤只有把計劃真的重播
    一遍才看得見。
    """
    coll = _random_collections(6, seed)
    steps = planning.plan(coll, list(coll), max_steps=4)
    replay(coll, steps)          # 不合法就會在這裡炸


@pytest.mark.parametrize("seed", range(6))
def test_每個人送出之後至少留一張(seed):
    """紅線 3：只有一張的卡不能送出。重播完不該有人的張數變成負的或歸零消失。"""
    coll = _random_collections(6, seed)
    steps = planning.plan(coll, list(coll), max_steps=4)
    after = replay(coll, steps)
    for tag, counts in after.items():
        for cid, n in counts.items():
            assert n >= 0, f"{tag} 的 {cid} 變成 {n}"
        # 原本有的卡不會因為送出而完全消失
        for cid, n0 in coll[tag].items():
            if n0 >= 1:
                assert after[tag].get(cid, 0) >= 1, f"{tag} 把最後一張 {cid} 送掉了"


# ── 步驟的意義 ─────────────────────────────────────────────────────


def test_同一步不會用到這一步才收到的卡():
    """同一步的交換要能同時進行，所以只能動用開始時就在手上的卡。

    違反的話清單就變成騙人的：使用者以為可以同時發訊息，實際上第二筆要等
    第一筆完成。
    """
    coll = _random_collections(6, 3)
    steps = planning.plan(coll, list(coll), max_steps=4)
    for i, batch in enumerate(steps):
        start = replay(coll, steps[:i])   # 前 i 步做完之後的狀態
        for tr in batch:
            # 用這一步開始時的張數判斷，完全不看同一步的其他交換
            assert start[tr["initiator"]].get(tr["gives"], 0) >= 2
            assert start[tr["receiver"]].get(tr["gets"], 0) >= 2


def _chained(coll, steps):
    """第二步以後，有沒有人送出一張他一開始根本送不出去的卡（開場 < 2 張）。

    釘機制而不是某一筆特定交換：寫死「第二步要有 #C 換 elixir-01」的話，
    搜尋挑到另一個同樣好的計劃就會假性失敗。
    """
    return [
        tr
        for batch in steps[1:]
        for tr in batch
        for giver, card in ((tr["initiator"], tr["gives"]), (tr["receiver"], tr["gets"]))
        if coll[giver].get(card, 0) < 2
    ]


@pytest.mark.parametrize("restarts", [1, planning.RESTARTS])
def test_鏈式交換會被排到下一步(restarts):
    """手上只有 1 張的卡送不出去（門檻 2 張）。先收下一張變成 2 張，
    下一步才送得掉 —— 這種先後關係正是「步驟」存在的理由。全部擠在同一步的話，
    第二筆在遊戲裡會直接失敗。

    兩種 restarts 都測：搜尋強度是會被調整的參數，不該讓它決定這條規則成不成立。
    這組資料是實測挑出來的（純單次貪婪與預設設定都會產生鏈）。
    """
    coll = _fixed_collections(5, 10, pool=12, sparse=0.45, top=3)
    steps = planning.plan(coll, list(coll), max_steps=4, restarts=restarts)
    replay(coll, steps)
    assert len(steps) >= 2, "應該要有第二步"
    assert _chained(coll, steps), "第二步以後沒有任何一筆是靠前面換來的卡才成立的"


def test_換不出東西時回空清單():
    """大家手上都只有一張，誰也送不出去。"""
    coll = {"#A": {E[0]: 1}, "#B": {E[1]: 1}}
    assert planning.plan(coll, list(coll)) == []


def test_步數上限有效():
    coll = _random_collections(6, 1)
    assert len(planning.plan(coll, list(coll), max_steps=1)) <= 1
    assert len(planning.plan(coll, list(coll), max_steps=2)) <= 2


def test_同樣的資料每次算出同樣的計劃():
    """順序不穩的話，使用者每次重新整理都看到不同的建議，會以為資料變了。"""
    coll = _random_collections(6, 7)
    a = planning.plan(coll, list(coll), max_steps=3)
    b = planning.plan(coll, list(coll), max_steps=3)
    assert a == b


# ── 搜尋品質 ───────────────────────────────────────────────────────


@pytest.mark.parametrize("seed", range(6))
def test_多重跑幾次不會比只跑一次差(seed):
    """種子固定成 0..n-1，所以 restarts=20 一定含 restarts=1 那一次。

    留不住這個保證的話，「多花十倍時間算」就可能算出更爛的答案。
    """
    coll = _random_collections(8, seed)
    tags = list(coll)
    one = planning.summarize(planning.plan(coll, tags, restarts=1))["total_new"]
    many = planning.summarize(planning.plan(coll, tags))["total_new"]
    assert many >= one


def _score(steps):
    """plan() 內部拿來比較的分數。

    刻意在這裡重寫一次而不是從 planning 匯入：這條測試要問的是「多一步值不值得」，
    值不值得的尺是**收穫**。把尺跟被測的程式綁在一起的話，改了計分方式兩邊會
    一起變，測試就永遠成立。
    """
    return sum(1 + (1 if tr["receiver_new"] else 0) for batch in steps for tr in batch)


@pytest.mark.parametrize("n,seed", [(5, 3), (8, 2), (8, 4), (12, 1), (17, 0)])
def test_多出來的步驟必須真的多換到東西(n, seed):
    """步數擺在筆數前面：同樣的收穫就不該叫人分兩步。

    多一步的代價是**全團要等第一步全部做完**才能開始第二步；多一筆只是多發
    一則訊息。實測過一組正式資料：62 筆兩步跟 63 筆一步換到的卡一模一樣 ——
    為了省一則訊息讓十二個人多等一輪，划不來。

    這條不綁任何特定的計劃長相，只問「第 k+1 步有沒有換到第 k 步換不到的」。
    """
    coll = _fixed_collections(n, seed)
    tags = list(coll)
    full = planning.plan(coll, tags, max_steps=3)
    for k in range(1, len(full)):
        fewer = planning.plan(coll, tags, max_steps=k)
        assert _score(full) > _score(fewer), (
            f"{len(full)} 步跟 {k} 步換到的一樣多，卻還是叫人多等 {len(full) - k} 輪"
        )


def test_搜尋品質不可以退回舊版():
    """釘住兩組實測過的資料。

    這兩個數字是拿掉「先消耗充裕的卡」那個排序、也拿掉重跑之後量到的：
    12 人 171、17 人 238。現在分別是 179 與 245。門檻設在中間，任何一邊被
    改回去都會掉下來 —— 而那種退步在畫面上完全看不出，只是每個人少換幾張。
    """
    for n, seed, floor in ((12, 12, 175), (17, 17, 242)):
        coll = _fixed_collections(n, seed)
        got = planning.summarize(planning.plan(coll, list(coll)))["total_new"]
        assert got >= floor, f"{n} 人只換到 {got} 張，低於實測基準 {floor}"


def _fixed_collections(n_players, seed, *, pool=None, sparse=0.4, top=4):
    """跟量測用的同一組資料產生器。

    參數（分布、卡池大小）會影響算出來的張數，而上面那條品質門檻是照這個
    分布量出來的 —— 改了參數就對不上了。
    """
    rng = random.Random(seed)
    ids = [c.id for c in cards.all_cards()]
    if pool:
        ids = ids[:pool]
    out = {}
    for i in range(n_players):
        counts = {}
        for cid in ids:
            r = rng.random()
            if r < sparse:
                continue
            counts[cid] = 1 if r < 0.8 else rng.randint(2, top)
        out[f"#P{i}"] = counts
    return out


def test_不會動到呼叫端的資料():
    """plan() 內部要就地改狀態，複製沒做乾淨的話會把資料庫讀出來的那份改掉。"""
    coll = _random_collections(5, 2)
    before = {t: dict(c) for t, c in coll.items()}
    planning.plan(coll, list(coll), max_steps=3)
    assert coll == before


# ── 優先照顧某人 ───────────────────────────────────────────────────


def test_指定優先對象時他補到的張數不會變少():
    """這個選項的全部意義就是「讓他多拿一點」。

    沒變多可以接受（資料本來就沒東西給他），變少就是選項反效果。
    """
    coll = _random_collections(6, 5)
    tags = list(coll)
    for who in tags:
        plain = planning.summarize(planning.plan(coll, tags, max_steps=3))
        favored = planning.summarize(planning.plan(coll, tags, max_steps=3, favor=who))
        assert favored["gained"].get(who, 0) >= plain["gained"].get(who, 0), (
            f"指定 {who} 之後他反而拿得更少"
        )


def test_優先對象的交換會排在最前面():
    """#B 跟 #C 之間也換得成，但指定 #A 之後，#A 的交換要先被安排 ——
    因為多的卡是有限的，誰先排到誰拿得到。
    """
    coll = {
        "#A": {E[0]: 2},
        "#B": {E[1]: 2, E[0]: 1},
        "#C": {E[1]: 1, E[2]: 2},
    }
    steps = planning.plan(coll, list(coll), max_steps=1, favor="#A")
    replay(coll, steps)
    assert steps, "指定優先對象之後反而算不出東西"
    assert any(tr["initiator"] == "#A" or tr["receiver"] == "#A" for tr in steps[0])


def _gain(steps, tag):
    """某個人補到幾張。跟 summarize() 同一套，只是這裡只要一個人的數字。"""
    return sum(
        1
        for batch in steps
        for tr in batch
        if tr["initiator"] == tag or (tr["receiver"] == tag and tr["receiver_new"])
    )


@pytest.mark.parametrize("seed", range(6))
def test_指定優先對象時多跑幾次只會讓他拿更多(seed):
    """種子是 0..n-1，所以 restarts=20 一定含 restarts=1 那一次。

    這條釘的是**挑選那一層有沒有把他放在前面**。候選那邊只留他發起的交換，
    但管不到「二十份候選計劃挑哪一份」—— 少了那層，同分時挑的是總分高的那份，
    而總分含接收方順便補到的，多跑幾次反而讓他自己拿得比較少。
    """
    coll = _fixed_collections(6, seed)
    tags = list(coll)
    for t in tags:
        few = planning.plan(coll, tags, favor=t, restarts=1)
        many = planning.plan(coll, tags, favor=t)
        assert _gain(many, t) >= _gain(few, t), f"多跑幾次之後 {t} 反而拿得更少"


@pytest.mark.parametrize("seed", range(6))
def test_指定補齊某人時清單裡每一筆都是他發起的(seed):
    """其他帳號之間不互相換卡。

    理由不是「先照顧他」而是「別人互換幫不到他」：他能補到的張數上限是
    `min(他的多餘張數, 他缺的當中別人拿得出來的種類數)`，別人之間換卡兩邊
    都推不動，反而會把只剩 2 張的供應者換成 1 張、供不出來。

    他當接收方的那些也不留 —— 那是他去補別人，對他零成本也零幫助。
    """
    coll = _fixed_collections(8, seed)
    tags = list(coll)
    steps = planning.plan(coll, tags, favor=tags[1])
    replay(coll, steps)
    for batch in steps:
        for tr in batch:
            assert tr["initiator"] == tags[1], (
                f"清單裡出現了 {tr['initiator']} 發起的交換，他不是被指定的人"
            )


@pytest.mark.parametrize("seed", range(6))
def test_不換其他人不會讓他拿得比較少(seed):
    """「非必要不互換」的那個「非必要」要有憑據。

    實測 258 個案例 0 次變差，這裡用同一個生成器守住不會回頭。
    """
    coll = _fixed_collections(8, seed)
    tags = list(coll)
    for t in tags:
        focused = _gain(planning.plan(coll, tags, favor=t), t)
        # 不指定的時候大家照常互換，他能拿到的不該比專門補他還多
        assert focused >= _gain(planning.plan(coll, tags), t)


def test_必要時會犧牲全體成全優先對象():
    """這個選項的意思是「補齊他」，不是「在不影響大家的前提下照顧他」。

    這組資料是實測挑出來的：以全體為主的挑法給 #P2 十四張，把他放到最前面
    之後是十五張。差一張，但差的那張正是這個選項存在的理由。
    """
    coll = _fixed_collections(6, 5)
    tags = list(coll)
    plain = planning.plan(coll, tags)
    favored = planning.plan(coll, tags, favor="#P2")
    assert _gain(favored, "#P2") == 15
    assert _gain(plain, "#P2") < 15


def test_優先對象也一樣要守規則():
    """加權只影響排序，不可以放寬紅線。"""
    coll = _random_collections(6, 9)
    tags = list(coll)
    steps = planning.plan(coll, tags, max_steps=4, favor=tags[0])
    replay(coll, steps)


# ── 統計 ───────────────────────────────────────────────────────────


def test_統計的張數跟重播的結果一致():
    """摘要跟清單對不起來的話，使用者會不知道該信哪個。"""
    coll = _random_collections(6, 4)
    steps = planning.plan(coll, list(coll), max_steps=3)
    after = replay(coll, steps)
    s = planning.summarize(steps)
    for tag, n in s["gained"].items():
        真的新增 = sum(
            1 for cid, cnt in after[tag].items() if cnt > 0 and coll[tag].get(cid, 0) == 0
        )
        assert n == 真的新增, f"{tag} 統計說補了 {n} 張，實際是 {真的新增} 張"
