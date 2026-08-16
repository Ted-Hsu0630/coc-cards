"""配對畫面：備選清單與方向標示。

這裡守的兩件事都是**只有使用者看得出來**的錯，後端測試一個都抓不到：

1. 「可改送」印出上面已經在送的那張卡。清單是按張數展開的，配對用掉前面幾份
   之後剩下的還是同一張，去重只去掉備選之間的重複，去不掉這種。使用者看到的是
   「可換 2 次」配上一張換不掉的備選 —— 照著做會發現遊戲裡送不出去。

2. 送出／收到只靠邊框顏色分，而顏色代表什麼畫面上哪裡都沒寫。記反了整份清單
   會整個讀反：把自己缺的卡當成要送出去的。

底下還有一條守多人配對的勾選框：那個 ✔ 不可以用字元畫。
"""

import re

import pytest

import config
from core import cards
from services import matching

WEB = config.BASE_DIR / "web"


@pytest.fixture(scope="module")
def app_js():
    return (WEB / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style_css():
    return (WEB / "style.css").read_text(encoding="utf-8")


def _elixir_ids():
    return [c.id for c in cards.all_cards() if c.series == "elixir"]


# ── 為什麼需要那個過濾 ─────────────────────────────────────────────


def test_可送出的清單真的會重複同一張卡():
    """前端那道過濾的存在理由。

    單向交換每換一次消耗一張，所以候選是按**張數**展開的（SPEC §6）。
    這條在後端釘住「同一張會出現不只一次」—— 哪天改成只列種類，
    前端那個 filter 就變成沒必要的贅碼，會有人順手刪掉；反過來
    如果這裡先壞了，前端的過濾也擋不住。
    """
    ids = _elixir_ids()
    mine = {ids[0]: 4}                      # 我只有一種卡，但有 4 張
    # 對方也有 1 張 ids[0] —— 少了這行就變成互利（我送的正好也補到他的空缺），
    # 而互利那側是不展開的，整條測試就測不到要測的東西
    theirs = {ids[0]: 1, ids[1]: 2, ids[2]: 2}

    res = matching.match_one(mine, theirs)
    assert res is not None
    kind, series, _, _ = res
    s = next(x for x in series if x.series == "elixir")

    assert kind == matching.INCOMING
    assert s.i_give.count(ids[0]) == 3, "4 張應該展開成 3 份可送"
    # 只能換 2 次（對方只有兩種我缺的），所以第 3 份是配不上的多餘資訊
    assert s.trades == 2
    assert s.i_give[s.trades :] == [ids[0]]


def test_備選扣掉已配對的之後就空了():
    """接續上一條：把前端的規則套上去，那張多餘的第 3 份不該留下來。

    規則寫在這裡是為了讓「應該長怎樣」有個可執行的定義；實作在 app.js 的
    alternatives()，由下面那條源碼測試釘住它沒有偷偷改回去。
    """
    ids = _elixir_ids()
    res = matching.match_one({ids[0]: 4}, {ids[0]: 1, ids[1]: 2, ids[2]: 2})
    s = next(x for x in res[1] if x.series == "elixir")

    used = set(s.i_give[: s.trades])
    rest = [c for c in dict.fromkeys(s.i_give[s.trades :]) if c not in used]
    assert rest == [], "唯一能送的卡已經在上面配對了，不該再列成『可改送』"


def test_真的還有別的可送時備選要留著():
    """反向：過濾不可以把真正的備選也吃掉，否則就變成沒得選了。"""
    ids = _elixir_ids()
    res = matching.match_one(
        {ids[0]: 4, ids[3]: 2}, {ids[0]: 1, ids[3]: 1, ids[1]: 2, ids[2]: 2}
    )
    s = next(x for x in res[1] if x.series == "elixir")

    used = set(s.i_give[: s.trades])
    rest = [c for c in dict.fromkeys(s.i_give[s.trades :]) if c not in used]
    assert ids[3] in rest


# ── 前端有沒有照做 ─────────────────────────────────────────────────


def test_備選有扣掉已經配對的卡(app_js):
    """光 new Set 只去掉備選之間的重複，去不掉「上面已經在送」的那張。"""
    src = app_js[app_js.index("function alternatives("):]
    src = src[: src.index("\n}")]
    assert "slice(0, trades)" in src, "沒有算出已經配對掉的那幾張"
    assert "!used.has(id)" in src, "算出來了卻沒有拿去過濾"


def test_備選超過上限要收起來(app_js):
    """「我受益」的對價可以是任何一張多的卡，列滿兩排卡面資訊量很低。"""
    assert "const ALT_SHOWN" in app_js
    src = app_js[app_js.index("function altRow("):]
    src = src[: src.index("\n}\n")]
    assert "slice(0, ALT_SHOWN)" in src
    assert "還有 ${rest.length} 張可選" in src
    # 收起來但要留得住 —— 直接砍掉會讓人以為只有前 4 張能選
    assert "addEventListener" in src and "more.remove()" in src


def test_配對列有寫出送出與收到(app_js):
    """方向不可以退回只靠邊框顏色。顏色的意思畫面上哪裡都沒寫。"""
    src = app_js[app_js.index("function renderSwap("):]
    src = src[: src.index("\n}\n")]
    assert '"give", "你送出"' in src
    assert '"get", "你收到"' in src

    # 多人配對那邊的方向是發起方的，不寫「你」，但一樣要有字
    plan = app_js[app_js.index("function renderTradeGroup("):]
    plan = plan[: plan.index("\n}\n")]
    assert '"give", "送出"' in plan
    assert '"get", "收到"' in plan


def test_那行字跟邊框同色(style_css):
    """同色才能當底下 faded 那幾排的圖例 —— 那幾排沒有字，只剩顏色可認。"""
    assert ".face.give .face-role { color: var(--warn); }" in style_css
    assert ".face.get .face-role { color: var(--ok); }" in style_css
    assert ".face.give img { border-color: var(--warn); }" in style_css
    assert ".face.get img { border-color: var(--ok); }" in style_css


# ── 多人配對的勾選框 ───────────────────────────────────────────────


def test_勾勾不可以用字元畫(style_css):
    """U+2714 在 iOS 上會 fallback 到另一套字型，筆畫細到幾乎看不見。

    這種壞法在桌機上完全看不出來 —— 同一份 CSS，桌機是粗實的勾、手機淡到
    分不出哪幾列勾了。用邊框畫的話粗細是我們說了算，各家瀏覽器一致。
    """
    # 只看 content:，註解裡寫 ✔ 說明是可以的
    for decl in re.findall(r"content:\s*([^;]+);", style_css):
        assert "✔" not in decl and "2714" not in decl, f"又用回字元畫勾了：{decl}"

    start = style_css.index('.check input[type="checkbox"]::after')
    block = style_css[start : style_css.index("}", start)]
    assert 'content: "";' in block
    assert "border: 2px solid currentColor" in block
    assert "rotate(45deg)" in block


def test_全選是勾選框而且會跟著人名同步(app_js):
    """一顆勾選框：全選了再點一下就全部清掉（購物車那樣）。

    關鍵是**同步**：手動取消其中一個之後，「全選」要跟著彈回未勾。沒同步的話
    它還顯示已勾，使用者以為再點一下是「補齊剩下的」，實際上是把全部清空。
    """
    src = app_js[app_js.index("function renderPlanPeople("):]
    src = src[: src.index("\n}\n")]
    assert "all.checked = list.length > 0 && list.every(" in src, "沒有跟著人名同步"
    assert "all.disabled = !list.length" in src, "名單空的時候全選要關掉"

    handler = app_js[app_js.index('$("#planAll").addEventListener'):]
    handler = handler[: handler.index("\n});")]
    assert 'addEventListener("change"' in handler, "要聽 change 才有得取消"
    assert "plan.picked.clear()" in handler, "取消勾選時沒有清空"
