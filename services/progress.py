"""讀畫面上方四個系列進度條的「N/M」。

用途有兩個：

1. **獨立檢查碼。** 辨識出來的收藏對不對，本來只能靠使用者自己看畫面比對。
   進度條是遊戲自己算的，跟我們的辨識完全無關，讀得出來就能自動驗證 ——
   「你這批截圖讀出聖水 12 張，但畫面上寫 14」這種話程式自己就講得出來。

2. **數字模板的來源。** 進度條跟徽章是同一套字體，但畫面上 1~9 都有，
   而且答案就寫在旁邊（`14/19` 本身就是標籤）。徽章只出現過 2、3、4，
   靠進度條才補得齊。實測純用進度條萃取的模板讀 64 個徽章：64/64 全對。

讀不出來就不回報，絕不猜 —— 檢查碼猜錯比沒有檢查碼更糟。
唯一的例外是收齊之後那顆「領取」按鈕，理由寫在 `claimable()`。
"""

import cv2
import numpy as np

from core import cards
from services import recognize as R

# 進度條長寬比實測 3.85~3.92（iPhone 與 iPad 都是）。卡片是 0.8，不會混淆。
BAR_AR = (3.2, 4.6)

# 白色筆畫的門檻。比徽章嚴很多：徽章底色是飽和金色，但米色面板會通過
# 徽章用的 `S<70 V>185`（實測米色 S=29 V=205），整段文字會連成一塊。
# 純白筆畫是 V=255 S=0，收到這裡才分得開。
WHITE_LO = np.array([0, 0, 245], np.uint8)
WHITE_HI = np.array([179, 20, 255], np.uint8)

# 「領取」按鈕的綠。系列的色相有四種，綠不在其中，所以不會跟進度條本體混到。
CLAIM_LO = np.array([35, 80, 80], np.uint8)
CLAIM_HI = np.array([85, 255, 255], np.uint8)
# 按鈕佔外框 25.3~25.5%，領完獎那個小綠勾只佔 2.1%，其餘進度條是 0.0%
# （實測 27 張截圖 × 4 條）。門檻放在中間差一個數量級的空檔裡。
CLAIM_MIN = 0.10


def find_bars(img) -> dict[str, tuple[int, int, int, int]]:
    """找出四個系列的進度條。回傳 {series: (x, y, w, h)}。

    進度條跟卡片用同一組色相，所以同一個遮罩就找得到 —— 差別只在長寬比。
    某個系列已收集完時，進度條會被「領取」按鈕取代：外框還在（找得到），
    但裡面沒有白色數字。那條交給 `claimable()` 認。
    """
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    k = R.nominal_kernel(W)
    out = {}
    for key, (lo, hi) in R.SERIES_HUE.items():
        mask = cv2.morphologyEx(R.border_mask(hsv, lo, hi), cv2.MORPH_CLOSE,
                                np.ones((k, k), np.uint8))
        best = None
        for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
            x, y, w, h = cv2.boundingRect(c)
            if w < W * 0.08 or h < H * 0.03:
                continue
            if BAR_AR[0] <= w / h <= BAR_AR[1] and (best is None or w * h > best[0]):
                best = (w * h, (x, y, w, h))
        if best:
            out[key] = best[1]
    return out


def bar_glyphs(img, box) -> list[dict]:
    """切出進度條裡的字形，由左到右。數字與斜線都會回傳。"""
    x, y, w, h = box
    # 數字在中央的金條上，左右留白避開兩側的圖示
    roi = img[y + int(h * 0.35):y + int(h * 0.85), x + int(w * 0.12):x + int(w * 0.72)]
    if roi.size == 0:
        return []
    white = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), WHITE_LO, WHITE_HI)

    n, lab, st, _ = cv2.connectedComponentsWithStats(white, 8)
    raw = []
    for i in range(1, n):
        L, T = int(st[i, cv2.CC_STAT_LEFT]), int(st[i, cv2.CC_STAT_TOP])
        cw, ch = int(st[i, cv2.CC_STAT_WIDTH]), int(st[i, cv2.CC_STAT_HEIGHT])
        if cw > ch * 1.4:                       # 字形比寬還高
            continue
        raw.append((L, cw, ch, (lab[T:T + ch, L:L + cw] == i).astype(np.uint8) * 255))
    if not raw:
        return []

    # 用**高度**篩，不用面積：斜線是細長的一撇，面積只有數字的一半，
    # 實測有一條剛好卡在面積門檻上被刷掉，整條進度條就讀不出來了。
    # 高度則所有字形都一致（數字與斜線都是同一個字級）。
    tallest = max(r[2] for r in raw)
    keep = [r for r in raw if r[2] >= tallest * 0.5]
    keep.sort(key=lambda r: r[0])
    return [{"x": r[0], "w": r[1], "h": r[2], "bitmap": r[3]} for r in keep]


MIN_SCORE = 0.55        # 與 recognize.match_digit 同一個門檻


def _match(bitmap, templates) -> str | None:
    """跟所有模板（含斜線）一起比，取最高分。

    不可以「先判斜線再判數字」—— 那等於給斜線優先權，
    某個數字剛好對斜線分數過門檻就會被吃掉。一起比才公平。
    """
    probe = cv2.resize(bitmap, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    probe = (probe - probe.mean()) / (probe.std() or 1)
    best, best_score = None, -1.0
    for label, tmpl in templates.items():
        t = (tmpl - tmpl.mean()) / (tmpl.std() or 1)
        s = float((probe * t).mean())
        if s > best_score:
            best, best_score = label, s
    return best if best_score >= MIN_SCORE else None


def read_bar(img, box, templates) -> tuple[int, int] | None:
    """讀一條進度條，回傳 (已收集, 總數)。任何一個字讀不出來就回 None。"""
    glyphs = bar_glyphs(img, box)
    if len(glyphs) < 3:                          # 最短是 N/M
        return None

    chars = []
    for g in glyphs:
        c = _match(g["bitmap"], templates)
        if c is None:
            return None
        chars.append(c)

    text = "".join(chars)
    if text.count("/") != 1:
        return None
    a, b = text.split("/")
    if not a or not b:
        return None
    return int(a), int(b)


def load_templates() -> dict[str, np.ndarray]:
    """數字 0~9 加上斜線。鍵是字元，因為進度條要拼成 "14/19" 這種字串。"""
    out = {str(k): v for k, v in R.load_digits().items()}
    p = R.BASE / "assets" / "digits" / "slash.png"
    if p.is_file():
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            out["/"] = cv2.resize(img, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    return out


def claimable(img, box) -> bool:
    """這條進度條是不是整條變成了「領取」按鈕。

    收齊一個系列之後，進度條會被一顆綠色的「領取」按鈕蓋掉，畫面上**連
    19/19 都不寫**。這是唯一「一個數字都讀不到、卻仍然知道答案」的情況 ——
    那顆按鈕只在收齊時出現，所以它本身就等於 N/N。不是猜的：
    groundtruth.json 的玩家 D 剛好聖水與闇黑兩個系列都長這樣，而他那 60 格
    的逐格張數獨立記在同一份檔案裡，兩個系列確實是 19/19 與 13/13。

    量的是**最大的單一綠色連通塊**佔外框的比例，不是綠色的總量：領完獎之後
    進度條會變回 19/19，右邊多一個綠勾，那個勾也是綠的。按鈕 25%、勾 2.1%，
    中間隔了一個數量級（見 CLAIM_MIN）。
    """
    x, y, w, h = box
    roi = img[y:y + h, x:x + w]
    if roi.size == 0:
        return False
    mask = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), CLAIM_LO, CLAIM_HI)
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n < 2:
        return False
    return max(int(st[i, cv2.CC_STAT_AREA]) for i in range(1, n)) >= w * h * CLAIM_MIN


def read_progress(img, templates=None) -> dict[str, tuple[int, int]]:
    """回傳讀得出來的系列進度。讀不出來的鍵**不會出現**，不會給 None 或 0。

    收齊的系列是唯一的例外：畫面上沒有數字可讀，但「領取」按鈕本身就說了
    答案，總數再從卡表補（紅線 8 保證卡表的張數是對的）。
    """
    templates = load_templates() if templates is None else templates
    if "/" not in templates:
        return {}                                # 沒有斜線模板就拼不出 N/M
    totals = {k: s["count"] for k, s in cards.series_meta().items()}
    out = {}
    for key, box in find_bars(img).items():
        # 先問按鈕再讀數字。反過來的話，「領取」那兩個白字有機會被切成字形
        # 去比模板 —— 拼出一個假的 N/M 是最糟的失敗方式。
        if claimable(img, box):
            if key in totals:
                out[key] = (totals[key], totals[key])
            continue
        got = read_bar(img, box, templates)
        if got:
            out[key] = got
    return out
