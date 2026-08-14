"""截圖辨識：從卡牌衝突的相簿截圖讀出每張卡的張數。

本機驗證中，**還沒接進 web app**。

設計要點（每條都是實測得來的，不是猜的 —— 見 tools/FINDINGS.md）：

* 所有幾何量都相對於「偵測到的卡片尺寸」，沒有任何絕對像素。
* 形態學核隨畫面寬度縮放。核太大會把相鄰同色卡片橋接成一團。
* **不靠卡面美術辨識是哪張卡。** 相簿順序固定、6 欄、無水平捲動，
  所以一張截圖必定是這個固定序列的連續視窗，起點是 6 的倍數。
  只要讀出每格的邊框顏色，顏色排列本身就唯一決定了視窗位置
  （9 個候選中的 8 個唯一；只有「整片聖水」那個視窗有兩個候選）。
* 判別門檻全部有實測的分離間隙，寫在各函式的註解裡。
"""

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parent.parent

# OpenCV 的 H 是 0..179
SERIES_HUE = {
    "elixir": (145, 162),
    "dark": (128, 145),
    "builder": (100, 118),
    "super": (5, 24),
}
S_MIN, V_MIN = 110, 110

COLS = 6                    # 相簿固定 6 欄，沒有水平捲動
AR_LO, AR_HI = 0.68, 0.98   # 卡片長寬比
RING = 0.14                 # 判系列用的外框環帶厚度

# 實測分離間隙：未擁有的 20 格 S95 全部精確為 0，已擁有的 40 格最低 83
OWNED_S95 = 40
# 實測分離間隙：無徽章 42 格全部 1.00，有徽章 18 格全部 ≤ 0.44。
# 已經不是判徽章的主訊號了，只留著診斷用（理由見 border_intact）
BADGE_BROKEN = 0.70

# 「切不出字形」要能解讀成「沒有徽章」，圖片得夠大。
# `x` 字形是卡片高度的固定比例（實測 20/230 ≈ 0.087），實測它掉到 8px 就開始
# 切不出來：卡高 214→x高 20、129→12、107→10、96→9、**86→8 就漏掉徽章**。
# 漏掉徽章會被讀成「1 張」而靜默寫錯，是最危險的失敗，所以門檻抓寬一點。
# 110px 對應約 1100px 寬的截圖 —— 真實手機／平板截圖都在 2200px 以上，
# 這道閘門正常情況不會觸發，只是防止有人上傳被縮很小的圖。
GLYPH_MIN_CARD_H = 110


# --- 有效性閘門 -------------------------------------------------------------
#
# 上傳非活動畫面的圖片絕對不可以污染資料。實測村莊畫面（滿滿彩色建築）會被
# 湊出 36 個「格子」，光靠「抓得到 6 欄」擋不住。以下三個判準各自都能分開，
# 一起用更保險（實測 10 張真相簿 vs 5 張非相簿）：
#
#   顏色吻合度   真相簿全部 100%       村莊畫面 39%
#   欄距/卡片寬   真相簿 1.41~1.45      村莊畫面 2.77
#   列數         真相簿 2~3            村莊畫面 6
#
PITCH_RATIO = (1.25, 1.65)
MIN_COLOR_MATCH = 0.85      # 12 格容許 1 格系列讀錯
MAX_ROWS = 4


@dataclass
class Result:
    """一張圖的辨識結果。ok=False 時 reason 說明為什麼不採用。"""
    ok: bool
    reason: str = ""
    start: int = -1
    hit: int = 0
    tied: list = None
    cells: list = None
    exact: bool = True       # 顏色排列是否完全吻合；False 代表定位把握較低

    def __post_init__(self):
        self.tied = self.tied if self.tied is not None else []
        self.cells = self.cells if self.cells is not None else []


@dataclass
class Cell:
    box: tuple[int, int, int, int]
    series: str
    owned: bool
    has_badge: bool
    count: int | None        # None = 讀不出來，要人工確認
    note: str = ""
    clip: str = ""           # "", "top", "bottom" —— 這格被畫面邊緣切掉的一側


def border_mask(hsv, lo, hi):
    lower = np.array([lo, S_MIN, V_MIN], np.uint8)
    upper = np.array([hi, 255, 255], np.uint8)
    return cv2.inRange(hsv, lower, upper)


def any_border_mask(hsv):
    out = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in SERIES_HUE.values():
        out |= border_mask(hsv, lo, hi)
    return out


# --- 格子偵測 ---------------------------------------------------------------


def nominal_kernel(W):
    """閉運算核的起手值。2622 寬 → 7，這是實測會過的值。"""
    return max(3, int(round(W * 0.0027)) | 1)


def kernel_candidates(W):
    """要試的核，由小到大。

    單一核不夠用：核太大會把相鄰同色卡片橋接成一團（實測 2622 寬用 9×9
    就全毀），太小則邊框斷裂。而「多大算大」不只取決於解析度 ——
    降採樣會讓邊框色和米色面板混合，某些尺度下縫隙會被填滿。
    實測同一張圖 0.55 倍正常、0.5 倍整片黏成一塊、0.45 倍又正常，
    完全是知識邊緣行為，算不出唯一正確的核。

    所以改成試幾個，取第一個能擬合出 6 欄的。驗證條件夠強
    （6 欄等距 + 寬度一致），試錯只會找到可用的核，不會找到錯的答案。
    """
    k0 = nominal_kernel(W)
    seen, out = set(), []
    for k in (k0, k0 - 2, k0 + 2, 3, k0 + 4):
        k = max(3, k | 1)
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def card_boxes(img, k=None):
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    k = k or nominal_kernel(W)
    closed = cv2.morphologyEx(any_border_mask(hsv), cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < W * 0.02 or h < H * 0.05:
            continue
        if AR_LO <= w / h <= AR_HI:
            boxes.append((x, y, w, h))
    if not boxes:
        return []
    # 面積收斂必須在長寬比**之後**，否則會被頂部進度條（長寬比 3.85）拉歪
    med = np.median([w * h for _, _, w, h in boxes])
    boxes = [b for b in boxes if 0.45 * med <= b[2] * b[3] <= 2.2 * med]
    if not boxes:
        return []

    # 再用**寬度**收斂一次。相簿沒有水平捲動，6 欄永遠完整可見，所以真卡片的
    # 寬度幾乎完全一致（實測各尺度的離散度都 < 1%）；高度則不能拿來濾，
    # 上下列會被面板邊緣切到（實測 214~245）。
    # 少了這道，降採樣時會混進一個尺寸略小的假框，把欄數推成 7 而整張失敗
    # ——實測 0.75 倍與 0.4 倍就是這樣掛的，而 1.0／0.5／0.33 倍卻正常，
    # 所以這個 bug 只掃描解析度才看得到，單一解析度測不出來。
    med_w = float(np.median([b[2] for b in boxes]))
    return [b for b in boxes if abs(b[2] - med_w) <= med_w * 0.05]


def _cluster(values, tol):
    out = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [float(np.median(g)) for g in out]


def _fit_columns(centers, expect=COLS):
    """從候選欄心裡挑出間距均勻的那 expect 個。

    降採樣時會混進假框而多出一欄（實測 0.75 倍的 IMG_4929 得到 7 欄，
    多出來的那欄距離前一欄 265，而真欄距是 205）。只靠尺寸濾不掉 ——
    那個假框的寬度只偏離中位數 4.9%，比誤差容忍還小。
    相簿的欄距實測完全均勻（原尺寸 273±0.4），所以擬合格線才是對的解法。
    """
    if len(centers) < expect:
        return None
    if len(centers) == expect:
        return centers

    pitch = float(np.median(np.diff(centers)))
    best = None
    for i in range(len(centers) - expect + 1):
        chain = [centers[i]]
        for c in centers[i + 1:]:
            if abs(c - chain[-1] - pitch) <= pitch * 0.25:
                chain.append(c)
        if len(chain) == expect:
            err = float(np.std(np.diff(chain)))
            if best is None or err < best[0]:
                best = (err, chain)
    return best[1] if best else None


def _raw_boxes(img, k=None):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    k = k or nominal_kernel(img.shape[1])
    closed = cv2.morphologyEx(any_border_mask(hsv), cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [cv2.boundingRect(c) for c in contours]


def grid(img):
    """回傳閱讀順序的 (bbox, clip)，clip 是 ""／"top"／"bottom"。

    用**每格自己的 bbox**，不可以用寬高中位數代替 —— 有徽章的卡片
    bbox 會往下長 6~7px（徽章是黃色，本身就被邊框遮罩抓進去），
    拿中位數去切會讓徽章掉在搜尋窗外。

    被畫面邊緣切到的列**不能直接丟掉**。實測兩張真實截圖各有 1~2 列
    被切（相簿面板上緣、下方獎勵條），長寬比分別是 1.30 / 3.64 / 1.06，
    全部落在完整卡片的 0.68~0.98 之外。只靠長寬比過濾會把它們靜默丟掉 ——
    使用者不會知道有 6~12 張沒被讀到，這比讀錯還難發現。

    所以改成兩遍：先用長寬比合格的完整卡片建立欄格線與完整高度，
    再把「寬度對、且在欄格線上」的框全部收回來，不管長寬比。
    寬度是可靠的判準：沒有水平捲動，6 欄永遠完整可見。
    """
    for k in kernel_candidates(img.shape[1]):
        full = card_boxes(img, k)
        if len(full) < COLS:
            continue
        w = float(np.median([b[2] for b in full]))
        col_x = _fit_columns(_cluster([b[0] for b in full], tol=w * 0.5))
        if col_x is not None:
            break
    else:
        return []

    h_full = float(np.median([b[3] for b in full]))
    on_lattice = [
        b for b in _raw_boxes(img, k)
        if abs(b[2] - w) <= w * 0.06 and min(abs(b[0] - x) for x in col_x) < w * 0.3
    ]
    if not on_lattice:
        return []

    rows: list[list] = []
    for b in sorted(on_lattice, key=lambda b: b[1]):
        if rows and b[1] - rows[-1][0][1] <= h_full * 0.5:
            rows[-1].append(b)
        else:
            rows.append([b])

    out = []
    for r, row in enumerate(rows):
        y = float(np.median([b[1] for b in row]))
        rh = float(np.median([b[3] for b in row]))
        # 只有最上面那列可能被上緣切、最下面那列可能被下緣切。
        # 中間的列若高度不足，代表偵測有問題，不當成被切。
        clip = ""
        if rh < h_full * 0.92:
            if r == 0:
                clip = "top"
            elif r == len(rows) - 1:
                clip = "bottom"
            else:
                continue
        for x in col_x:
            near = [b for b in row if abs(b[0] - x) < w * 0.5]
            out.append((near[0] if near else (int(x), int(y), int(w), int(rh)), clip))
    return out


# --- 每格的判讀 -------------------------------------------------------------


def cell_series(hsv, box):
    """只用左右兩側邊條判系列。

    不可以拿整格比色 —— 卡面美術會蓋過邊框（實測全聖水的一列被讀成
    S/E/S/E/E/S，因為野蠻人的頭髮、巨人的膚色都是橘黃色）。
    也不用上下邊：底部會被徽章切斷。
    """
    x, y, w, h = box
    cell = hsv[y:y + h, x:x + w]
    side = np.hstack([cell[:, :int(w * 0.08)], cell[:, int(w * 0.92):]])
    scores = {k: int(border_mask(side, *v).sum()) for k, v in SERIES_HUE.items()}
    best = max(scores, key=scores.get)
    total = sum(scores.values()) or 1
    return best, scores[best] / total


def cell_owned(hsv, box, clip="", h_full=None):
    """未擁有的卡是真灰階，飽和度精確為 0。回傳 (是否擁有或 None, S95)。

    不可以用飽和度**中位數** —— 熔岩獵犬（灰岩）中位數 49、
    冰霸王（白冰）中位數 13，兩張都確實擁有。要用高百分位。

    被切到的格子，取樣範圍必須照**完整卡片高度**推算，不能照可視高度：
    實測一張下緣被切、只剩 50px 的灰階卡，照可視高度取樣會取到卡片上方
    的藍色邊框，S95 衝高而誤判成「已擁有」。可視部分太少時回 None，
    寧可交給人工也不要猜。
    """
    x, y, w, h = box
    hf = h_full or h
    # 上緣被切時底邊才是真的，用它反推卡片真正的上緣
    top = y + h - hf if clip == "top" else y
    a0, a1 = top + int(hf * 0.15), top + int(hf * 0.66)
    a0, a1 = max(a0, y), min(a1, y + h)          # 夾回可視範圍
    if a1 - a0 < hf * 0.51 * 0.25:               # 有效取樣不到預期的四分之一
        return None, 0.0

    roi = hsv[a0:a1, x + int(w * 0.18):x + int(w * 0.82)]
    if roi.size == 0:
        return None, 0.0
    p95 = float(np.percentile(roi[:, :, 1], 95))
    return p95 > OWNED_S95, p95


def border_intact(hsv, box, series):
    """底部邊框中央還剩多少沒被蓋住。**已經不是判徽章的主訊號**，只留著診斷用。

    這招在 iPhone 的無損截圖上分得很開（無徽章 42 格全 1.00、有徽章 18 格
    全 ≤0.44），但換到 iPad + LINE 壓縮就塌了，原因有兩個：

    1. JPEG 讓邊框顏色溢到卡片下緣之外，`max()` 會抓到卡片外面的縫隙，
       中央當然是 0，於是誤判成「有徽章」。實測 4 格中招。
    2. **超級部隊的橘色邊框和徽章金色落在同一個色相區間**，
       對橘卡而言「徽章蓋住邊框」這個前提本來就不成立。

    第 2 點沒得修，所以改用字形切割當主訊號（見 badge_glyphs）。
    """
    x, y, w, h = box
    cell = hsv[y:y + h, x:x + w]
    m = border_mask(cell, *SERIES_HUE[series]) > 0
    L, R = int(w * 0.06), int(w * 0.94)
    # 卡片真正的底邊 = 左右兩側邊條同時還是邊框色的最後一列。
    # 側邊條不會被徽章蓋到，所以這個定位不受徽章影響。
    cand = [r for r in range(int(h * 0.5), h) if m[r, :L].any() and m[r, R:].any()]
    if not cand:
        return False, 1.0
    bot = max(cand)
    intact = float(m[bot - 2:bot + 1, int(w * 0.32):int(w * 0.68)].mean())
    return intact < BADGE_BROKEN, intact


def badge_glyphs(img, box):
    """切出徽章裡的字形。回傳 (x字形, [數字字形...]) 的灰階圖，失敗回 None。

    **這是判斷「有沒有徽章」的主訊號**：切得出字形就是有徽章。
    實測 167 格已擁有的卡（iPhone 無損 50 格有 ground truth ＋
    iPad LINE 壓縮 117 格有進度條檢查碼）：
    64 個真徽章全部抓到、0 漏；103 個無徽章全部正確、0 誤判。

    比「底部邊框被切斷」可靠得多 —— 那招在超級部隊上失效
    （橘邊框與金徽章同色相），理由見 border_intact 的註解。

    `x` 字形實測永遠是正方形，可以拿來當**尺度基準** ——
    數字高度除以 x 高度是尺度不變量，跨解析度直接免疫。
    """
    x, y, w, h = box
    roi = img[y + int(h * 0.78):y + h, x + int(w * 0.24):x + int(w * 0.76)]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, np.array([0, 0, 185], np.uint8), np.array([179, 70, 255], np.uint8))

    n, lab, st, _ = cv2.connectedComponentsWithStats(white, 8)
    parts = []
    for i in range(1, n):
        L, T = int(st[i, cv2.CC_STAT_LEFT]), int(st[i, cv2.CC_STAT_TOP])
        cw, ch = int(st[i, cv2.CC_STAT_WIDTH]), int(st[i, cv2.CC_STAT_HEIGHT])
        if st[i, cv2.CC_STAT_AREA] < white.size * 0.015:
            continue
        # 字形比寬還高（或接近正方）。實測冰霸王的白色美術會溢出成 85x22 的橫塊
        if cw > ch * 1.4:
            continue
        bitmap = (lab[T:T + ch, L:L + cw] == i).astype(np.uint8) * 255
        parts.append({"x": L, "cy": T + ch / 2, "w": cw, "h": ch,
                      "area": int(st[i, cv2.CC_STAT_AREA]), "bitmap": bitmap})
    if len(parts) < 2:
        return None

    # 徽章的字形共用基線，會垂直對齊；從上方溢進來的卡面美術不會。
    # 實測 x4 與其中一張 x2 各多出一個「夠大又夠瘦」的雜訊元件，
    # 都貼在 ROI 頂端（y=0），單靠大小與長寬比濾不掉。
    tol = float(np.median([p["h"] for p in parts])) * 0.4
    groups = []
    for p in sorted(parts, key=lambda p: p["cy"]):
        if groups and p["cy"] - groups[-1][-1]["cy"] <= tol:
            groups[-1].append(p)
        else:
            groups.append([p])
    group = max(groups, key=lambda g: sum(p["area"] for p in g))
    if len(group) < 2:
        return None

    group.sort(key=lambda p: p["x"])
    as_tuple = [(p["x"], p["w"], p["h"], p["bitmap"]) for p in group]
    return as_tuple[0], as_tuple[1:]


def read_glyph_count(glyphs, digits):
    """讀徽章數字。認不出來回 None（交給人工確認，不要猜）。"""
    _, digit_parts = glyphs
    out = 0
    for _, _, _, bitmap in digit_parts:
        d = match_digit(bitmap, digits)
        if d is None:
            return None
        out = out * 10 + d
    return out if out >= 2 else None


def read_count(img, box, digits):
    g = badge_glyphs(img, box)
    return read_glyph_count(g, digits) if g else None


def match_digit(bitmap, digits, min_score=0.55):
    """把字形正規化到固定尺寸後跟模板比，取最高分。"""
    if not digits:
        return None
    probe = cv2.resize(bitmap, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    probe = (probe - probe.mean()) / (probe.std() or 1)
    best, best_score = None, -1.0
    for value, tmpl in digits.items():
        t = (tmpl - tmpl.mean()) / (tmpl.std() or 1)
        score = float((probe * t).mean())
        if score > best_score:
            best, best_score = value, score
    return best if best_score >= min_score else None


def load_digits() -> dict[int, np.ndarray]:
    """只載入數字。同一個資料夾還有 slash.png，那是給進度條拼 "14/19" 用的，
    徽章上不會出現斜線，混進來只會多一個永遠不該中的候選。"""
    d = BASE / "assets" / "digits"
    out = {}
    if d.is_dir():
        for p in sorted(d.glob("*.png")):
            if not p.stem.isdigit():
                continue
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                out[int(p.stem)] = cv2.resize(img, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    return out


# --- 視窗推導 ---------------------------------------------------------------


def album_series() -> list[str]:
    data = json.loads((BASE / "assets" / "cards.json").read_text(encoding="utf-8"))
    out = []
    for s in data["series"]:
        out += [s["key"]] * s["count"]
    return out


def album_ids() -> list[str]:
    data = json.loads((BASE / "assets" / "cards.json").read_text(encoding="utf-8"))
    return [c["id"] for c in data["cards"]]


ART_SIZE = 64


def art_patch(img, box):
    """切出卡面美術，轉灰階後正規化。

    避開邊框與底部徽章。零均值單位變異數的正規化很重要：同一張卡在
    「已擁有（彩色）」與「未擁有（灰階）」兩種狀態下整體亮度差很多，
    正規化之後比對的是結構而不是亮度。
    """
    x, y, w, h = box
    roi = img[y + int(h * 0.12):y + int(h * 0.70), x + int(w * 0.12):x + int(w * 0.88)]
    if roi.size == 0:
        return None
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (ART_SIZE, ART_SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)
    g = (g - g.mean()) / (g.std() or 1)
    return cv2.normalize(g, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)


def load_art() -> dict[str, np.ndarray]:
    d = BASE / "assets" / "art"
    out = {}
    if d.is_dir():
        for p in sorted(d.glob("*.png")):
            a = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if a is not None:
                f = a.astype(np.float32)
                out[p.stem] = (f - f.mean()) / (f.std() or 1)
    return out


def art_score(img, cells, start, art, ids):
    """這批格子當成從 start 開始時，卡面跟模板有多像。

    **被畫面切到的格子要跳過。** art_patch 是照可視框推算裁切範圍的，
    格子被切掉時那個範圍根本不是卡面，比出來是雜訊。雜訊對每個候選視窗
    都差不多，所以不會指錯答案，但會把差距稀釋掉 —— 實測 IMG_ZERO
    （下緣 6 格被切）跳過之後差距從 0.270 回到 0.280，是唯一有差的一張。

    整批都被切時退回全部算：稀釋過的分數還是比沒有分數好。
    要不要跳只看 cells，跟 start 無關 —— 不然不同候選會用不同的格子集合，
    分數就不能互相比了。
    """
    usable = [(i, c) for i, c in enumerate(cells) if not c.clip] or list(enumerate(cells))
    total, n = 0.0, 0
    for i, c in usable:
        card = ids[start + i] if start + i < len(ids) else None
        tmpl = art.get(card) if card else None
        if tmpl is None:
            continue
        patch = art_patch(img, c.box)
        if patch is None:
            continue
        f = patch.astype(np.float32)
        f = (f - f.mean()) / (f.std() or 1)
        total += float((f * tmpl).mean())
        n += 1
    return total / n if n else -1.0


def infer_window(observed: list[str], album: list[str] | None = None):
    """推導這些格子對應相簿的哪一段。回傳 (起點, 吻合數, 候選數)。

    起點必為 6 的倍數（沒有水平捲動），所以候選很少。
    候選數 > 1 代表顏色排列分不出來 —— 實測只有「整片聖水」會這樣。
    """
    album = album or album_series()
    n = len(observed)
    scored = []
    for k in range(0, len(album) - n + 1, COLS):
        hit = sum(a == b for a, b in zip(album[k:k + n], observed, strict=False))
        scored.append((hit, k))
    scored.sort(key=lambda t: (-t[0], t[1]))
    top = scored[0][0]
    tied = [k for hit, k in scored if hit == top]
    return tied[0], top, tied


# --- 主流程 -----------------------------------------------------------------


def check_geometry(boxes):
    """幾何有效性。回傳不通過的原因，通過回 None。"""
    n_rows = len(boxes) // COLS
    if n_rows > MAX_ROWS:
        return f"排出了 {n_rows} 列，相簿一次最多只看得到 {MAX_ROWS} 列"

    ws = [b[2] for b, _ in boxes]
    w = float(np.median(ws))
    cols = _cluster([b[0] for b, _ in boxes], tol=w * 0.5)
    if len(cols) != COLS:
        return f"排不出 {COLS} 欄"
    ratio = float(np.median(np.diff(sorted(cols)))) / w
    if not (PITCH_RATIO[0] <= ratio <= PITCH_RATIO[1]):
        return f"欄距與卡片寬的比例是 {ratio:.2f}，相簿應該在 {PITCH_RATIO[0]}~{PITCH_RATIO[1]}"
    return None


def recognize(img, digits=None, art=None) -> Result:
    """辨識一張圖。ok=False 時 reason 說明為什麼不採用。"""
    digits = load_digits() if digits is None else digits
    art = load_art() if art is None else art
    boxes = grid(img)
    if not boxes:
        return Result(False, "找不到相簿的 6 欄卡片格線")

    bad = check_geometry(boxes)
    if bad:
        return Result(False, bad)

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # 完整卡片的高度。card_boxes 保證至少有一整列沒被切，所以取最大值就對了
    h_full = max(b[3] for b, _ in boxes)

    cells = []
    for box, clip in boxes:
        series, _ = cell_series(hsv, box)
        owned, _ = cell_owned(hsv, box, clip, h_full)

        if owned is None:
            cells.append(Cell(box, series, False, False, None, "被切太多，判不出有沒有擁有", clip))
            continue

        if clip == "bottom":
            # 下緣被切＝底部邊框與徽章都不在畫面裡，字形當然切不出來，
            # 但「切不出字形」在這裡不能解讀成「沒有徽章」——
            # 徽章可能只是被畫面切掉了。寧可回報認不出，也不要猜成 1 張。
            has_badge = False
            count = 0 if not owned else None
            note = "" if not owned else "下緣被切到，看不到徽章"
        elif not owned:
            has_badge, count, note = False, 0, ""
        else:
            glyphs = badge_glyphs(img, box)
            has_badge = glyphs is not None
            if has_badge:
                count = read_glyph_count(glyphs, digits)
                note = "" if count else "徽章數字認不出來"
            elif h_full < GLYPH_MIN_CARD_H:
                # 圖片太小，字形本來就切不出來，這時「沒字形」不代表「沒徽章」
                count, note = None, "圖片太小，分不出 1 張還是多張"
            else:
                count, note = 1, ""
        cells.append(Cell(box, series, owned, has_badge, count, note, clip))

    start, hit, tied = infer_window([c.series for c in cells])
    match = hit / len(cells)
    if match < MIN_COLOR_MATCH:
        # 顏色排列對不上任何一個候選視窗 —— 這就不是相簿畫面。
        # 實測村莊畫面只有 39%，真相簿全部 100%。
        return Result(False, f"邊框顏色排列只吻合 {match:.0%}，對不上相簿的任何一段")

    return Result(True, "", start, hit, tied, cells, exact=(hit == len(cells)))


def resolve_batch(results, images=None, art=None):
    """多張截圖一起定位，用「同一本相簿的視窗不該重疊」化解單張的歧義。

    這比看卡面可靠得多，而且不需要任何模板：實測 5 張截圖裡有 4 張的
    邊框顏色排列本身就唯一，它們釘住了相簿第 13~60 張，剩下那張
    「整片聖水」的兩個候選（第 1~12 / 第 7~18 張）只有一個不衝突。

    只有單張上傳、又剛好是整片聖水時才會退回卡面比對。
    """
    taken = set()
    for r in results:
        if r.ok and len(r.tied) == 1:
            taken.update(range(r.start, r.start + len(r.cells)))

    for i, r in enumerate(results):
        if not r.ok or len(r.tied) <= 1:
            continue
        free = [k for k in r.tied if not (set(range(k, k + len(r.cells))) & taken)]
        if len(free) == 1:
            r.start = free[0]
        elif art and images is not None:
            ids = album_ids()
            pool = free or r.tied
            r.start = max(pool, key=lambda k: art_score(images[i], r.cells, k, art, ids))
        taken.update(range(r.start, r.start + len(r.cells)))
    return results
