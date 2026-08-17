"""探針：找出頂部四個系列進度條，並切出裡面的「N/M」字形。

目的有兩個：
1. 進度條的數字跟徽章是同一套字體，但畫面上 0~9 十個都有，
   而且**答案就寫在旁邊**（`14/19` 本身就是標籤）—— 拿來補齊徽章缺的 5~9 模板。
2. 讀得出進度條，就能把「檢查碼」從人工核對變成程式自動驗證。

跑法：
    .venv/bin/python tools/probe_bars.py
"""

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import recognize as R  # noqa: E402

BASE = Path(__file__).resolve().parent.parent

# 進度條的長寬比實測 3.85（四個都一樣）。卡片是 0.8，差很遠，不會混淆。
BAR_AR = (3.2, 4.6)


def find_bars(img):
    """用邊框色遮罩找進度條。回傳 {series: (x, y, w, h)}。

    進度條跟卡片用同一組色相（洋紅／深紫／藍／橘），所以同一個遮罩就找得到，
    只是長寬比完全不同 —— 卡片 0.8、進度條 3.85。
    """
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    k = R.nominal_kernel(W)
    out = {}
    for key, (lo, hi) in R.SERIES_HUE.items():
        m = cv2.morphologyEx(R.border_mask(hsv, lo, hi), cv2.MORPH_CLOSE,
                             np.ones((k, k), np.uint8))
        cands = []
        for c in cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
            x, y, w, h = cv2.boundingRect(c)
            if w < W * 0.08 or h < H * 0.03:
                continue
            if BAR_AR[0] <= w / h <= BAR_AR[1]:
                cands.append((w * h, (x, y, w, h)))
        if cands:
            out[key] = max(cands)[1]
    return out


def bar_glyphs(img, box):
    """切出進度條裡的白色字形。

    門檻要比徽章嚴很多：徽章底色是飽和金色，米色面板卻會通過
    `S<70 V>185`（實測米色 S=29 V=205），整段文字會連成一塊。
    純白筆畫是 V=255 S=0，收到 `S<20 V>245` 才分得開。
    """
    x, y, w, h = box
    # 數字在進度條中央的金條上，左右各留一點避開圖示
    roi = img[y + int(h * 0.35):y + int(h * 0.85), x + int(w * 0.12):x + int(w * 0.72)]
    if roi.size == 0:
        return []
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, np.array([0, 0, 245], np.uint8), np.array([179, 20, 255], np.uint8))

    n, lab, st, _ = cv2.connectedComponentsWithStats(white, 8)
    parts = []
    for i in range(1, n):
        L, T = int(st[i, cv2.CC_STAT_LEFT]), int(st[i, cv2.CC_STAT_TOP])
        cw, ch = int(st[i, cv2.CC_STAT_WIDTH]), int(st[i, cv2.CC_STAT_HEIGHT])
        if st[i, cv2.CC_STAT_AREA] < white.size * 0.008:
            continue
        if cw > ch * 1.4:
            continue
        parts.append({"x": L, "w": cw, "h": ch,
                      "bitmap": (lab[T:T + ch, L:L + cw] == i).astype(np.uint8) * 255})
    parts.sort(key=lambda p: p["x"])
    return parts


def main():
    names = sorted(p.stem for p in (BASE / "samples").glob("IMG_49*")
                   if p.suffix.upper() in (".PNG", ".JPG"))
    for name in names:
        p = next((BASE / "samples").glob(f"{name}.*"))
        img = cv2.imread(str(p))
        bars = find_bars(img)
        print(f"\n=== {name}  {img.shape[1]}x{img.shape[0]} ===")
        if not bars:
            print("  沒找到進度條")
            continue
        for key in ("elixir", "dark", "builder", "super"):
            if key not in bars:
                print(f"  {key:8s} —（已收集完，進度條變成「領取」按鈕）")
                continue
            x, y, w, h = bars[key]
            g = bar_glyphs(img, bars[key])
            print(f"  {key:8s} bar={w}x{h} ar={w / h:.2f}  字形 {len(g)} 個 "
                  f"{[(q['w'], q['h']) for q in g]}")


if __name__ == "__main__":
    main()
