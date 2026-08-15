"""截圖辨識的安全性質。

這裡守的不是「準確率高不高」（那是 tools/evaluate.py 的事），
而是**錯誤的失敗方式不會發生**：

1. 非活動畫面的圖片不可以被當成相簿，會污染別人的收藏資料
2. 讀不出來要回報「認不出」，不可以猜一個具體數字
3. 沒有截圖涵蓋到的卡要標成 uncovered，不可以預設成 0
4. 不同截圖給出矛盾的值時要標成 conflict，不可以隨便挑一個
"""

import json
import pathlib

import pytest

cv2 = pytest.importorskip("cv2", reason="辨識功能需要 opencv，沒裝就跳過")

# 這兩行必須在 importorskip 之後，所以 E402 是刻意的
from services import importer  # noqa: E402
from services import recognize as R  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = BASE / "samples"
GT = json.loads((BASE / "tools" / "groundtruth.json").read_text(encoding="utf-8"))
ALBUM = sorted(k for k in GT if not k.startswith("_"))


def _path(name):
    """iPhone 組是 .PNG、iPad 組是 LINE 壓縮過的 .JPG。"""
    for ext in (".PNG", ".JPG"):
        p = SAMPLES / f"{name}{ext}"
        if p.exists():
            return p
    raise FileNotFoundError(f"samples/{name}.(PNG|JPG)")


def _img(name):
    img = cv2.imread(str(_path(name)))
    assert img is not None, f"讀不到 {name}"
    return img


# --- 1. 不可以被非相簿畫面污染 -----------------------------------------------


@pytest.mark.parametrize("name", ["IMG_4933", "IMG_4934", "IMG_4935"])
def test_遊戲其他畫面會被拒絕(name):
    """村莊畫面滿滿彩色建築，實測會被湊出 36 個假格子 —— 必須擋下來。"""
    r = R.recognize(_img(name))
    assert not r.ok
    assert r.reason, "拒絕時一定要說得出原因，否則使用者不知道要換哪張圖"


def test_教學用的照片會被拒絕():
    for p in (BASE / "web" / "img").glob("*.jpg"):
        img = cv2.imread(str(p))
        assert not R.recognize(img).ok, f"{p.name} 不該被當成相簿"


def test_純色與雜訊不會被當成相簿():
    import numpy as np

    rng = np.random.default_rng(0)
    for img in (
        np.zeros((1200, 2600, 3), np.uint8),
        np.full((1200, 2600, 3), 200, np.uint8),
        rng.integers(0, 255, (1200, 2600, 3), dtype=np.uint8),
    ):
        assert not R.recognize(img).ok


def test_不是圖片的檔案會被拒絕而且不影響其他檔案():
    res = importer.analyze([
        ("好的.png", _path("IMG_4926").read_bytes()),
        ("壞的.txt", b"not an image"),
    ])
    ok = [f for f in res["files"] if f["ok"]]
    assert len(ok) == 1 and ok[0]["name"] == "好的.png"
    assert res["summary"]["by_state"]["read"] == 12


# --- 2. 真的相簿要被接受，而且位置要對 ---------------------------------------


@pytest.mark.parametrize("name", ALBUM)
def test_真相簿會被接受且視窗位置正確(name):
    r = R.recognize(_img(name))
    assert r.ok, f"{name} 被誤拒：{r.reason}"
    assert r.exact, "真相簿的邊框顏色排列應該完全吻合"
    R.resolve_batch([r], images=[_img(name)], art=R.load_art())
    assert r.start == GT[name]["start"]


# --- 3. 讀不出來要拒答，不可以猜 ---------------------------------------------


@pytest.mark.parametrize("name", ALBUM)
def test_絕不讀出與事實不符的張數(name):
    """整份測資裡不允許出現任何一個「讀錯」。認不出可以，讀錯不行。"""
    img = _img(name)
    r = R.recognize(img)
    R.resolve_batch([r], images=[img], art=R.load_art())
    for i, (c, truth) in enumerate(zip(r.cells, GT[name]["counts"], strict=True)):
        if truth is None:
            assert c.count is None, f"{name}[{i}] 被切太多卻猜了 {c.count}"
        else:
            assert c.count in (truth, None), f"{name}[{i}] 讀成 {c.count}，實際 {truth}"


def test_圖片太小時不會硬猜張數():
    """縮到字形讀不出來的尺寸，有徽章的卡不可以被讀成 1 張。"""
    img = cv2.resize(_img("IMG_4926"), None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA)
    r = R.recognize(img)
    if not r.ok:
        return                      # 整張拒絕也是可接受的失敗方式
    R.resolve_batch([r], images=[img], art=R.load_art())
    for i, (c, truth) in enumerate(zip(r.cells, GT["IMG_4926"]["counts"], strict=True)):
        assert c.count in (truth, None), f"[{i}] 低解析度下讀成 {c.count}，實際 {truth}"


# --- 4. 合併時要誠實 ---------------------------------------------------------


def test_沒被截圖涵蓋的卡標成_uncovered_而不是零():
    res = importer.analyze([("一張.png", _path("IMG_4926").read_bytes())])
    rows = {r["id"]: r for r in res["cards"]}
    assert rows["elixir-01"]["state"] == "read"
    late = rows["super-17"]
    assert late["state"] == "uncovered"
    assert late["value"] is None, "沒涵蓋到就不能給值，給 0 等於謊報未擁有"
    assert res["summary"]["by_state"]["uncovered"] == 48


def test_兩張截圖矛盾時標成_conflict():
    """實際會發生：成員不小心把別人的截圖一起傳上來。"""
    res = importer.analyze([
        ("我的.png", _path("IMG_4926").read_bytes()),
        ("別人的.png", _path("IMG_4942").read_bytes()),
    ])
    rows = {r["id"]: r for r in res["cards"]}
    # 兩張都涵蓋到 elixir-19，而兩人的收藏不同
    assert rows["elixir-19"]["state"] in ("conflict", "read")
    conflicts = [r for r in res["cards"] if r["state"] == "conflict"]
    for c in conflicts:
        assert c["value"] is None, "有衝突就不能挑一個當答案"
        assert len(c["sources"]) >= 2 and c["note"]


def test_系列統計在有格子沒讀到時不給數字():
    """報一個偏低的總數會讓人以為辨識錯了，不如不報。"""
    res = importer.analyze([("一張.png", _path("IMG_4926").read_bytes())])
    by_key = {s["key"]: s for s in res["summary"]["series_owned"]}
    assert by_key["elixir"]["owned"] is None and by_key["elixir"]["missing"] > 0
    assert by_key["super"]["owned"] is None


def test_全部讀到時才給可對照進度條的數字():
    res = importer.analyze([
        (f"{n}.png", _path(n).read_bytes())
        for n in ["IMG_4926", "IMG_4927", "IMG_4928", "IMG_4929", "IMG_4930"]
    ])
    by_key = {s["key"]: s for s in res["summary"]["series_owned"]}
    # groundtruth.json 記載這位玩家的進度條是 14/19、8/13、9/11、9/17
    assert by_key["elixir"]["owned"] == 14
    assert by_key["dark"]["owned"] == 8
    assert by_key["builder"]["owned"] == 9
    assert by_key["super"]["owned"] == 9


# --- 5. 跨裝置：iPad + LINE 壓縮 ---------------------------------------------
#
# 這條路徑壞過兩次，兩次都只有真的換裝置才看得出來：
#   - 「徽章切斷底部邊框」在超級部隊的橘卡上失效（橘邊框與金徽章同色相）
#   - JPEG 讓邊框色溢出卡片下緣，底邊定位抓到卡片外面的縫隙
# 所以這組測試存在的意義是「不准再退回去」。

IPAD = sorted(k for k in GT if not k.startswith("_") and GT[k].get("snapshot"))
PROGRESS = GT["_progress"]


def _read_player(player):
    """把一位玩家的整組截圖讀成 card_id -> 張數。"""
    names = [n for n in IPAD if GT[n]["player"] == player]
    imgs = [_img(n) for n in names]
    results = [R.recognize(i) for i in imgs]
    R.resolve_batch(results, images=imgs, art=R.load_art())
    ids = R.album_ids()
    out, unknown = {}, 0
    for r in results:
        assert r.ok, f"{player} 有截圖被誤拒：{r.reason}"
        for i, c in enumerate(r.cells):
            if c.count is None:
                unknown += 1
            else:
                out[ids[r.start + i]] = c.count
    return out, unknown


@pytest.mark.parametrize("player", sorted(PROGRESS))
def test_iPad截圖對得上遊戲內進度條(player):
    """**獨立於快照**的驗證：進度條數字是人從遊戲畫面抄下來的。

    快照可能整組一起漂掉而測不出來，但進度條不會 —— 它是外部事實。
    """
    counts, unknown = _read_player(player)
    assert unknown == 0, f"{player} 有 {unknown} 格沒讀出來"
    assert len(counts) == 60, f"{player} 只讀到 {len(counts)} 格"

    bar = PROGRESS[player]
    for key, meta in cards_meta().items():
        owned = sum(1 for c in meta["ids"] if counts.get(c, 0) > 0)
        assert owned == bar[key], f"{player} 的 {key} 讀出 {owned} 張，進度條是 {bar[key]}"
    assert sum(1 for v in counts.values() if v > 0) == bar["total"]


def cards_meta():
    from core import cards as C

    out = {}
    for c in C.all_cards():
        out.setdefault(c.series, {"ids": []})["ids"].append(c.id)
    return out


@pytest.mark.parametrize("name", IPAD)
def test_iPad截圖的每格值沒有漂掉(name):
    """回歸基準。值變了不一定是壞了，但一定要有人看過再更新快照。"""
    img = _img(name)
    r = R.recognize(img)
    assert r.ok, f"{name} 被誤拒：{r.reason}"
    R.resolve_batch([r], images=[img], art=R.load_art())
    assert r.start == GT[name]["start"]
    assert [c.count for c in r.cells] == GT[name]["counts"]


def test_跨裝置的長寬比確實不同():
    """這組測資如果哪天被換成同一台裝置拍的，上面那些測試就失去意義了。"""
    a = _img("IMG_4926")            # iPhone
    b = _img("IMG_4943")            # iPad
    ra, rb = a.shape[1] / a.shape[0], b.shape[1] / b.shape[0]
    assert abs(ra - rb) > 0.5, f"兩組的長寬比太接近（{ra:.2f} vs {rb:.2f}），測不到跨裝置"


# --- 6. 進度條檢查碼 ---------------------------------------------------------
#
# 這是整套系統唯一的**獨立**驗證：進度條的數字是遊戲自己算的，
# 跟我們的辨識毫無關係。辨識結果跟它對得上，才是真的有意義的正確性證據；
# 自己跟自己比對永遠會過。

from services import progress as P  # noqa: E402

BARS = GT["_bars"]


@pytest.mark.parametrize("name", ALBUM)
def test_進度條讀出來的數字跟人工抄的一致(name):
    img = _img(name)
    got = P.read_progress(img)
    want = BARS[GT[name]["player"]]
    for key, pair in want.items():
        if pair is None:
            # 該系列已收集完，進度條變成「領取」按鈕 —— 讀不出來才是對的
            assert key not in got, f"{key} 應該讀不出值，卻讀成 {got[key]}"
        else:
            assert key in got, f"{key} 讀不出來（正解 {pair[0]}/{pair[1]}）"
            assert list(got[key]) == pair, f"{key} 讀成 {got[key]}，正解 {tuple(pair)}"


def test_非相簿畫面讀不出進度條():
    for name in ("IMG_4933", "IMG_4934", "IMG_4935"):
        assert P.read_progress(_img(name)) == {}


def test_辨識結果與進度條一致():
    """完整上傳一位玩家的五張截圖，四個系列都要對得上。"""
    res = importer.analyze([
        (f"{n}.jpg", _path(n).read_bytes())
        for n in ["IMG_4948", "IMG_4949", "IMG_4950", "IMG_4951", "IMG_4952"]
    ])
    groups = res["summary"]["series_owned"]
    compared = [g for g in groups if g["owned"] is not None and g["expected"] is not None]
    assert len(compared) == 4, "四個系列都該有可比對的數字"
    for g in compared:
        assert g["owned"] == g["expected"], f"{g['name']} 讀到 {g['owned']}，畫面上是 {g['expected']}"


def test_混到別人的截圖時進度條會互相矛盾():
    """成員把別人的截圖一起傳上來是實際會發生的事。

    這時進度條會互相打架，寧可不給檢查碼也不要給錯的 —— 給錯的比沒有更糟。
    """
    res = importer.analyze([
        ("我的.jpg", _path("IMG_4948").read_bytes()),
        ("別人的.jpg", _path("IMG_4953").read_bytes()),
    ])
    noted = [g for g in res["summary"]["series_owned"] if g["bar_note"]]
    assert noted, "進度條互相矛盾時要說出來"
    for g in noted:
        assert g["expected"] is None, "有矛盾就不可以挑一個當答案"


def test_徽章數字模板涵蓋_0_到_9():
    """模板原本只有 2、3、4（徽章上就只出現過這三個數字）。
    補上進度條當來源之後才湊齊，x5 以上不再一律標成認不出。"""
    digits = R.load_digits()
    assert set(digits) == set(range(10)), f"缺 {sorted(set(range(10)) - set(digits))}"
    assert "/" in P.load_templates(), "進度條要拼出 N/M 需要斜線模板"


def test_進度條裡的_0_讀得出來():
    """0 是最晚才補齊的模板，而且原本 22 張截圖的進度條裡一個 0 都沒有 ——
    分子是 14/8/9/2/1/17/12/7/6/5，分母是 19/13/11/17。

    IMG_ZERO 是唯一含 0 的一張（新玩家，三個系列都是 0），
    同時也是第三種解析度。少了它，0 這個模板等於從沒被驗證過。
    """
    got = P.read_progress(_img("IMG_ZERO"))
    assert got == {"elixir": (1, 19), "dark": (0, 13), "builder": (0, 11), "super": (0, 17)}


def test_三種解析度都涵蓋到():
    """測資如果哪天全換成同一台裝置拍的，跨裝置的測試就變成擺設。"""
    ratios = sorted(round(_img(n).shape[1] / _img(n).shape[0], 2)
                    for n in ("IMG_4926", "IMG_4943", "IMG_ZERO"))
    assert len(set(ratios)) == 3, f"長寬比重複了：{ratios}"
    assert max(ratios) - min(ratios) > 0.6, f"三者太接近：{ratios}"


# --- 8. 整片聖水的二選一：只有卡面比對能解 ------------------------------------
#
# 相簿第 1~12 張與第 7~18 張都是 12 張聖水，邊框顏色排列完全一樣。
# 多張一起上傳時「視窗不重疊」就化解掉了，單張上傳只剩卡面比對可用。
#
# 卡面模板只從玩家 A 萃取（tools/extract_art.py），所以拿 A 的截圖測等於
# 作弊。這裡只用其他玩家的 —— 不同帳號、不同收藏狀態、不同裝置。

ART_INDEPENDENT = [n for n in ALBUM if GT[n]["player"] != "A" and GT[n]["start"] == 0]


@pytest.mark.parametrize("name", ART_INDEPENDENT)
def test_單張整片聖水靠卡面比對定位(name):
    img = _img(name)
    r = R.recognize(img, art={})
    # 先確認這張真的有歧義。少了這句，哪天顏色排列變得唯一，
    # 下面的斷言會在什麼都沒測到的情況下照樣綠燈。
    assert r.tied == [0, 6], f"{name} 應該有兩個候選視窗，實際 {r.tied}"

    art, ids = R.load_art(), R.album_ids()
    scores = {k: R.art_score(img, r.cells, k, art, ids) for k in r.tied}
    want = GT[name]["start"]
    assert max(scores, key=scores.get) == want, f"卡面比對選了 #{max(scores, key=scores.get)}"

    other = [v for k, v in scores.items() if k != want]
    margin = scores[want] - max(other)
    # 實測最小差距 0.280（IMG_ZERO，玩家幾乎沒收集所以整片灰階）。
    # 門檻訂在 0.15 是為了讓「差距在縮小」提早被發現，不是等到選錯才知道。
    assert margin > 0.15, f"{name} 兩個候選只差 {margin:.3f}，太接近了"


def _by_id(r):
    ids = R.album_ids()
    return {ids[r.start + i]: c for i, c in enumerate(r.cells) if r.start + i < len(ids)}


def _resolved(name):
    img = _img(name)
    r = R.recognize(img)
    R.resolve_batch([r], images=[img], art=R.load_art())
    return r


def test_灰色美術的卡不會被判成沒擁有():
    """加農炮戰車是灰色金屬砲管，只有背景與底部繩索有顏色。

    用第 95 百分位量只有 39，門檻 40 就差一分被判成沒擁有 ——
    而「有卡卻讀成沒有」是會靜默寫錯資料的方向。

    它是擁有的這件事不必靠肉眼：畫面上進度條寫建築 8/11，這張截圖涵蓋
    builder-05~11，視窗外只剩 builder-01~04 四張，所以視窗內至少要有 4 張。
    少了加農炮戰車就只剩 3 張，算術上湊不到 8。
    """
    r = _resolved("IMG_4996")
    cell = _by_id(r)["builder-07"]
    assert cell.owned, "加農炮戰車被判成沒擁有"
    assert cell.count == 1

    ids = R.album_ids()
    owned = sum(1 for i, c in enumerate(r.cells)
                if ids[r.start + i].startswith("builder") and c.owned)
    assert owned >= 4, f"進度條說建築 8/11，視窗內至少要 4 張才湊得出來，實際只有 {owned}"


def test_擁有判定的分離間隙夠寬():
    """釘住「用哪個統計量」這個決定，而不只是釘住結論。

    S95 曾經讓加農炮戰車以 39 對 40 落敗。改用 S99 之後間隙拉開到 111，
    哪天有人為了別的理由把百分位調回去，這裡會先炸。
    """
    owned_lo, unowned_hi = 255.0, 0.0
    for name in ALBUM:
        img = _img(name)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        boxes = R.grid(img)
        hf = max(b[3] for b, _ in boxes)
        for (box, clip), truth in zip(boxes, GT[name]["counts"], strict=True):
            if truth is None or clip:
                continue                      # 被切的格子另有規則
            _, sat = R.cell_owned(hsv, box, clip, hf)
            if truth > 0:
                owned_lo = min(owned_lo, sat)
            else:
                unowned_hi = max(unowned_hi, sat)

    assert unowned_hi == 0, f"未擁有的卡應該是真灰階，實際量到 {unowned_hi}"
    assert owned_lo > R.OWNED_SAT * 2, f"已擁有最低 {owned_lo}，離門檻 {R.OWNED_SAT} 太近"


def test_整批格子都被切時卡面比對不會失效():
    """art_score 跳過被切的格子，但整批都被切時必須退回全部算 ——
    不然每個候選都拿不到分數，比較就變成「挑第一個」而不是挑最像的。"""
    img = _img("IMG_4943")
    r = R.recognize(img, art={})
    for c in r.cells:
        c.clip = "bottom"
    art, ids = R.load_art(), R.album_ids()
    scores = {k: R.art_score(img, r.cells, k, art, ids) for k in r.tied}
    assert min(scores.values()) > -1.0, "整批被切就算不出分數，等於沒有裁判"
    assert max(scores, key=scores.get) == GT["IMG_4943"]["start"]
