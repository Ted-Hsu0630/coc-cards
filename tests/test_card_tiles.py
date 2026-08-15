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


def block(src, marker):
    """回傳 marker 之後那一對大括號裡的內容。

    切到第一個 `});` 為止會出事：saveBtn 的處理器裡有個
    `JSON.stringify({ ... })` 也長那樣，切出來的東西根本不含要驗的那一行 ——
    而測試照樣「通過」了大半。只數大括號、而且從**函式主體**的那個 `{`
    開始數（不能連小括號一起數，`$("#saveBtn")` 的括號會先收平）。
    """
    start = src.index("{", src.index(marker))
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start : j + 1]
    raise AssertionError(f"{marker} 的大括號沒有收平")


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
    imp = block(app_js, "function importTile")
    assert "allowUnset: true" in imp, "匯入確認的未填狀態不見了"

    # 收藏頁那一段不可以出現 allowUnset —— 出現了就是有人在那裡也打開了它
    coll = block(app_js, "function renderCollection")
    assert "allowUnset" not in coll


def test_減到零之後的去向兩邊不同(app_js):
    """收藏頁按到 0 就停在 0；匯入確認要能再按一次退回未填。

    寫成同一種行為的話，其中一個畫面一定是錯的。
    """
    dec = block(app_js, 'dec.addEventListener("click"')
    assert "allowUnset ? null : 0" in dec, "0 之後的去向沒有分辨兩個畫面"


def test_一張不標數字兩張以上才標(app_js):
    """跟遊戲內一致。1 張標 x1 會讓整片畫面都是徽章，反而看不出誰是多的 ——
    而「多出來的可以送人」正是這個站在算的東西。
    """
    assert "v >= 2 ? `x${v}`" in app_js


def test_欄數跟著寬度走不寫死():
    """一開始寫死 6 欄，手機上每格只剩 49px —— 圖太小、名字也塞不下。

    改成 auto-fill 讓欄數自己跟著寬度長。最小格寬是這裡唯一的旋鈕：72px 在
    375px 的手機上排得下 4 欄，調大會掉到 3 欄。
    """
    css = (WEB / "style.css").read_text(encoding="utf-8")
    grid = css[css.index(".grid {") :]
    grid = grid[: grid.index("}")]
    m = re.search(r"repeat\(\s*auto-fill\s*,\s*minmax\(\s*(\d+)px", grid)
    assert m, grid
    min_px = int(m.group(1))

    # 375px 手機：main 內距 12x2、card 內距 14x2，欄距 6px
    avail = 375 - 12 * 2 - 14 * 2
    cols = (avail + 6) // (min_px + 6)
    assert cols == 4, f"手機上會排成 {cols} 欄"


def test_減號跟徽章的大小跟著格子走():
    """格子從手機的 76px 到桌機的 130px 以上都有。固定 px 的減號在大圖上會
    小到按不太到 —— cqw 是格子寬度的百分比，比例才會一致。
    """
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert "container-type: inline-size" in css, "沒有這個，cqw 會找不到參考對象"
    dec = css[css.index(".dec {") :]
    dec = dec[: dec.index("}")]
    assert "cqw" in dec, "減號還是固定大小"


def test_格子不吃雙擊縮放():
    """連點加數量時，瀏覽器預設會等一下判斷是不是雙擊（要放大）：每一下都
    慢半拍，連點還會真的把畫面放大。
    """
    css = (WEB / "style.css").read_text(encoding="utf-8")
    for sel in (".tile {", ".dec {"):
        rule = css[css.index(sel) :]
        rule = rule[: rule.index("}")]
        assert "touch-action: manipulation" in rule, f"{sel} 少了 touch-action"


def test_進度條是改寬度不是重建(app_js):
    """CSS transition 要有舊值才補得出動畫。每次都把整列重畫的話，收到新卡時
    進度只會瞬間跳過去，動畫完全不會發生。
    """
    upd = block(app_js, "function updateProgress")
    assert "textContent = \"\"" not in upd, "updateProgress 又在重建元素了"
    assert "setBar" in upd

    css = (WEB / "style.css").read_text(encoding="utf-8")
    fill = css[css.index(".bar-fill {") :]
    fill = fill[: fill.index("}")]
    assert "transition" in fill and "width" in fill


def test_未填的第一下是零不是一(app_js):
    """「認不出」跟「我看過了，這張是 0 張」都是常見結果。第一下就跳到 1 的話，
    要表達 0 得先加到上限再減回來（減不到 -1）。
    """
    click = block(app_js, 'tile.addEventListener("click"')
    assert "allowUnset ? 0 : 1" in click


def test_boot_可以被呼叫第二次(app_js):
    """boot() 不是只跑一次：登入成功、加綁村莊、切換村莊都會再呼叫一次。

    「載入中」那塊在第一次就被移掉了，第二次寫成 `.remove()` 會對 null 呼叫，
    丟 TypeError。而登入那次的呼叫包在 try/catch 裡 —— 結果是**登入其實成功了，
    畫面卻顯示一行 JS 錯誤並停在登入頁**。實際踩過。
    """
    assert app_js.count("await boot()") > 1, "boot() 本來就會被呼叫多次，這條才有意義"
    assert "$(\"#booting\").remove()" not in app_js, "第二次呼叫 boot() 會炸"
    assert app_js.count('$("#booting")?.remove()') == 2


def test_取消會回到伺服器上那一份(app_js):
    """存過之後 state.saved 沒跟著更新的話，按取消會退回**上上次**的資料，
    而且畫面不會有任何錯誤 —— 使用者只會發現剛存的東西不見了。
    """
    assert "state.saved = { ...state.counts }" in app_js
    save = block(app_js, '$("#saveBtn").addEventListener')
    assert "state.saved" in save, "存檔成功後沒有更新 state.saved"
