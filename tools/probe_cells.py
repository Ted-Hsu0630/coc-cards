"""探針：從截圖找出卡片格子、判定系列、推導這張截圖對應相簿的哪一段。

本機驗證用，**不屬於 web app**，也不會進 Docker 映像檔。

實測筆記（推翻了 SPEC §7.2 的兩個結論）：

1. 相鄰同色卡片的邊框「會不會橋接」取決於閉運算核的大小，不是必然。
   核 9×9 時 12 格黏成一團、外輪廓全毀；核 7×7 時 12 格乾淨分離。
   所以核**一定要隨畫面尺寸縮放**，寫死像素就會在別的解析度上爆掉。

2. 系列判定不可以拿整格去比色 —— 卡面美術會蓋過邊框。
   實測 4926 第一列明明全是聖水（洋紅），整格比色卻讀成 S/E/S/E/E/S，
   因為野蠻人的頭髮、巨人的膚色、氣球兵的火焰都是橘黃色。
   **只取外框環**（外緣 14% 的環帶）之後才對。

跑法：
    .venv/bin/python tools/probe_cells.py samples/*.PNG
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parent.parent

# OpenCV 的 H 是 0..179。SPEC §7.2.1 實測值。
SERIES_HUE = {
    "elixir": (145, 162),
    "dark": (128, 145),
    "builder": (100, 118),
    "super": (5, 24),
}
S_MIN, V_MIN = 110, 110

COLS = 6                    # 相簿固定 6 欄，沒有水平捲動
AR_LO, AR_HI = 0.68, 0.98   # 卡片長寬比，上下被切到時會偏高
RING = 0.14                 # 外框環帶佔卡片寬高的比例


def album_series() -> list[str]:
    """相簿順序的系列序列，共 60 個。"""
    data = json.loads((BASE / "assets" / "cards.json").read_text(encoding="utf-8"))
    out = []
    for s in data["series"]:
        out += [s["key"]] * s["count"]
    return out


def border_mask(hsv, lo, hi):
    lower = np.array([lo, S_MIN, V_MIN], np.uint8)
    upper = np.array([hi, 255, 255], np.uint8)
    return cv2.inRange(hsv, lower, upper)


def any_border_mask(hsv):
    out = np.zeros(hsv.shape[:2], np.uint8)
    for lo, hi in SERIES_HUE.values():
        out |= border_mask(hsv, lo, hi)
    return out


def card_boxes(img):
    """抓出像卡片的外輪廓。回傳 (x, y, w, h) 清單。"""
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # 核隨畫面縮放：2622 寬時得到 7，這是實測會過的值
    k = max(3, int(round(W * 0.0027)) | 1)
    closed = cv2.morphologyEx(any_border_mask(hsv), cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < W * 0.02 or h < H * 0.05:      # 太小的一律是背景雜訊
            continue
        if AR_LO <= w / h <= AR_HI:
            boxes.append((x, y, w, h))
    if not boxes:
        return []

    # 面積收斂放在長寬比**之後**（SPEC §7.2：順序顛倒會被頂部進度條拉歪）
    med = np.median([w * h for _, _, w, h in boxes])
    return [b for b in boxes if 0.45 * med <= b[2] * b[3] <= 2.2 * med]


def cluster(values, tol):
    out = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [float(np.median(g)) for g in out]


def ring_series(hsv, x, y, w, h):
    """只用外框環帶判定系列 —— 拿整格比色會被卡面美術帶偏。"""
    x, y, w, h = int(x), int(y), int(w), int(h)
    roi = hsv[y:y + h, x:x + w]
    if roi.size == 0:
        return "?", 0.0

    ring = np.ones(roi.shape[:2], bool)
    mx, my = int(w * RING), int(h * RING)
    ring[my:h - my, mx:w - mx] = False       # 挖掉中央的美術區

    scores = {}
    for name, (lo, hi) in SERIES_HUE.items():
        m = border_mask(roi, lo, hi).astype(bool) & ring
        scores[name] = int(m.sum())

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    return (best, scores[best] / total) if total else ("?", 0.0)


def infer_window(observed: list[str], album: list[str]):
    """推導這 N 格對應相簿的哪一段。

    相簿沒有水平捲動，所以視窗起點必定是 6 的倍數 —— 候選只有 9 個
    （60 張 = 6 欄 × 10 列，一次看得到 2 列）。回傳 (起點, 吻合數, 是否唯一)。
    """
    n = len(observed)
    best = []
    for k in range(0, len(album) - n + 1, COLS):
        hit = sum(a == b for a, b in zip(album[k:k + n], observed, strict=False))
        best.append((hit, k))
    best.sort(reverse=True)
    top = best[0]
    unique = len(best) == 1 or best[1][0] < top[0]
    return top[1], top[0], unique


def main(paths):
    album = album_series()
    for p in paths:
        img = cv2.imread(str(p))
        if img is None:
            print(f"{p}: 讀不到")
            continue
        H, W = img.shape[:2]
        boxes = card_boxes(img)

        print(f"\n=== {Path(p).name}  {W}x{H} ===")
        if len(boxes) < COLS:
            print(f"  只抓到 {len(boxes)} 格，不是相簿畫面")
            continue

        w_med = float(np.median([b[2] for b in boxes]))
        h_med = float(np.median([b[3] for b in boxes]))
        col_x = cluster([b[0] for b in boxes], tol=w_med * 0.5)
        row_y = cluster([b[1] for b in boxes], tol=h_med * 0.5)
        pitches = np.diff(col_x)

        print(f"  {len(boxes)} 格 / {len(col_x)} 欄 × {len(row_y)} 列   w={w_med:.0f} h={h_med:.0f}")
        if len(pitches):
            print(f"  欄距 {pitches.mean():.1f} ± {pitches.std():.1f}")
        if len(col_x) != COLS:
            print(f"  ⚠ 欄數不是 {COLS}，格網推導不可信")
            continue

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        observed, weakest = [], 1.0
        for y in row_y:
            for x in col_x:
                s, conf = ring_series(hsv, x, y, w_med, h_med)
                observed.append(s)
                weakest = min(weakest, conf)

        letters = "".join(s[0].upper() for s in observed)
        rows = [letters[i:i + COLS] for i in range(0, len(letters), COLS)]
        start, hit, unique = infer_window(observed, album)
        expect = "".join(s[0].upper() for s in album[start:start + len(observed)])
        expect_rows = [expect[i:i + COLS] for i in range(0, len(expect), COLS)]

        print(f"  讀到 {rows}   最低把握 {weakest:.0%}")
        print(f"  推導 {expect_rows}   起點 #{start}  吻合 {hit}/{len(observed)}  唯一={unique}")
        if hit == len(observed):
            print(f"  ✓ 完全吻合 → 這 {len(observed)} 格是相簿第 {start + 1}~{start + len(observed)} 張")


if __name__ == "__main__":
    main(sys.argv[1:])
