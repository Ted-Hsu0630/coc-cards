"""卡片格子：圖檔、對照表、以及那個「未填」不可以消失。

這一整塊改動裡有兩種安靜的壞法：

1. **圖對不上** —— 卡表加了新卡、或圖檔改了命名規則，畫面上就是一格破圖，
   沒有錯誤訊息、沒有 console 警告，只有使用者看到一個叉叉。
2. **未填被吃掉** —— 匯入確認的「這格我不知道」跟「這格是 0 張」是兩回事
   （CLAUDE.md 紅線 10）。改成點擊式之後這個區別只剩下一個 allowUnset
   參數在守，很容易在之後的重構裡被順手拿掉。
"""

import importlib.util
import re
import struct

import pytest

import config
from core import cards

WEB = config.BASE_DIR / "web"
ICONS = WEB / "img" / "cards"


@pytest.fixture(scope="module")
def app_js():
    return (WEB / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def entity_map():
    """直接載入 tools/ 的腳本拿它的對照表。

    複製一份到測試裡就失去意義了 —— 兩邊會一起錯。
    """
    path = config.BASE_DIR / "tools" / "fetch_card_icons.py"
    spec = importlib.util.spec_from_file_location("fetch_card_icons", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ENTITY


# ── 圖檔 ───────────────────────────────────────────────────────────


def test_每張卡都有圖而且沒有多餘的檔案():
    """檔名就是卡片 id —— 前端靠這個規則直接拼 URL，不查表。

    少一張是破圖，多一張是卡表刪過而圖沒清掉。兩邊都要抓。
    """
    have = {p.stem for p in ICONS.glob("*.png")}
    want = {c.id for c in cards.all_cards()}
    assert have - want == set(), f"多出來的圖：{sorted(have - want)}"
    assert want - have == set(), f"少了圖：{sorted(want - have)}"


def test_圖檔真的是_png_而且不是空的():
    """抓圖時對方回 404 頁面或錯誤訊息的話，檔案照樣會被寫出來。"""
    for p in sorted(ICONS.glob("*.png")):
        head = p.read_bytes()[:24]
        assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{p.name} 不是 PNG"
        w, h = struct.unpack(">II", head[16:24])
        assert w >= 100 and h >= 100, f"{p.name} 只有 {w}x{h}，太小"


def test_對照表跟卡表一致(entity_map):
    """卡表加了新卡而對照表沒跟上的話，重跑抓圖腳本只會安靜地少抓一張。"""
    ids = {c.id for c in cards.all_cards()}
    assert set(entity_map) == ids, (
        f"對照表缺 {sorted(ids - set(entity_map))}，多 {sorted(set(entity_map) - ids)}"
    )
    assert len(set(entity_map.values())) == len(entity_map), "有兩張卡對到同一個來源圖"


def test_前端拼圖片路徑的規則跟檔名一致(app_js):
    """這條規則寫在兩個地方（腳本存檔、前端拼 URL），改一邊就會全部破圖。"""
    assert "/static/img/cards/${card.id}.png" in app_js


# ── 互動 ───────────────────────────────────────────────────────────


def test_匯入確認保留未填而收藏頁沒有(app_js):
    """紅線 10：認不出的格子要留白，不可以猜 0 或 1。

    收藏頁反過來 —— 那裡的最小值就是 0（「我沒有這張」），多一個未填狀態
    只會讓人困惑。所以是 importTile 明確要求 allowUnset，cardTile 預設關閉。
    """
    assert re.search(r"function cardTile\([^)]*allowUnset = false", app_js), (
        "cardTile 的 allowUnset 預設不是 false，收藏頁會多出未填狀態"
    )
    imp = app_js[app_js.index("function importTile") :]
    imp = imp[: imp.index("\n}\n")]
    assert "allowUnset: true" in imp, "匯入確認的未填狀態不見了"

    # 收藏頁那一段不可以出現 allowUnset —— 出現了就是有人在那裡也打開了它
    coll = app_js[app_js.index("function renderCollection") :]
    coll = coll[: coll.index("\n}\n")]
    assert "allowUnset" not in coll


def test_減到零之後的去向兩邊不同(app_js):
    """收藏頁按到 0 就停在 0；匯入確認要能再按一次退回未填。

    寫成同一種行為的話，其中一個畫面一定是錯的。
    """
    dec = app_js[app_js.index('dec.addEventListener("click"') :]
    dec = dec[: dec.index("});")]
    assert "allowUnset ? null : 0" in dec, "0 之後的去向沒有分辨兩個畫面"


def test_一張不標數字兩張以上才標(app_js):
    """跟遊戲內一致。1 張標 x1 會讓整片畫面都是徽章，反而看不出誰是多的 ——
    而「多出來的可以送人」正是這個站在算的東西。
    """
    assert "v >= 2 ? `x${v}`" in app_js


def test_排六欄():
    """遊戲內相簿就是 6 個一行。改成 auto-fill 的話手機與桌機會排出不同的
    欄數，跟遊戲畫面對照就對不起來了。
    """
    css = (WEB / "style.css").read_text(encoding="utf-8")
    grid = css[css.index(".grid {") :]
    grid = grid[: grid.index("}")]
    assert re.search(r"grid-template-columns:\s*repeat\(6\s*,", grid), grid
