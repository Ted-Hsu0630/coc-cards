"""探針：量測「有沒有擁有」與「幾張」的判別特徵。

本機驗證用，不屬於 web app。用 groundtruth.json 對答案，門檻由資料決定。

跑法：
    .venv/bin/python tools/probe_counts.py
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_cells import COLS, card_boxes, cluster  # noqa: E402

BADGE_LO = np.array([16, 110, 140], np.uint8)
BADGE_HI = np.array([40, 255, 255], np.uint8)


def cells(img):
    """回傳閱讀順序的 (x, y, w, h)，用**每格實際的 bbox**。

    不可以用寬高中位數代替：有徽章的卡片 bbox 會往下長 6~7px
    （徽章是黃色，本身就被邊框遮罩抓進去），拿中位數去切會讓
    第二列的徽章掉在搜尋窗外面 —— 實測漏掉法師與飛龍的 x2。
    """
    boxes = card_boxes(img)
    if len(boxes) < COLS:
        return []
    w = float(np.median([b[2] for b in boxes]))
    h = float(np.median([b[3] for b in boxes]))
    col_x = cluster([b[0] for b in boxes], tol=w * 0.5)
    row_y = cluster([b[1] for b in boxes], tol=h * 0.5)

    out = []
    for y in row_y:
        for x in col_x:
            near = [b for b in boxes if abs(b[0] - x) < w * 0.5 and abs(b[1] - y) < h * 0.5]
            out.append(near[0] if near else (int(x), int(y), int(w), int(h)))
    return out


def art_features(hsv, box):
    """卡面美術的飽和度。未擁有＝真灰階，S 精確為 0。"""
    x, y, w, h = box
    roi = hsv[y + int(h * 0.15):y + int(h * 0.66), x + int(w * 0.18):x + int(w * 0.82)]
    if roi.size == 0:
        return 0.0, 0.0, 0.0
    s = roi[:, :, 1]
    return float(np.median(s)), float(np.percentile(s, 95)), float((s > 60).mean())


def badge_blob(hsv, box):
    """在卡片底部找徽章。回傳 (w, h, 置中偏移比) 或 None。

    不能只看「有沒有黃色」—— 實測弓箭手沒徽章但底部有 37% 黃色像素
    （綠衣＋金項鍊）。要看的是**形狀**：置中、寬約卡片一半的矩形塊。
    """
    x, y, w, h = box
    band = hsv[y + int(h * 0.74):y + h, x:x + w]
    if band.size == 0:
        return None
    m = cv2.inRange(band, BADGE_LO, BADGE_HI)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    n, _, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for i in range(1, n):
        bw, bh, area = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], stats[i, cv2.CC_STAT_AREA]
        off = abs(cent[i][0] - w / 2) / w
        if area < w * h * 0.01:
            continue
        if best is None or area > best[3]:
            best = (bw / w, bh / h, off, area)
    return best[:3] if best else None


def main():
    gt = json.loads((BASE / "tools" / "groundtruth.json").read_text(encoding="utf-8"))
    print(f"{'檔案':12s} {'格':3s} {'真實':4s} {'S中位':6s} {'S95':5s} {'彩色比':6s} "
          f"{'徽w':5s} {'徽h':5s} {'偏移':5s}")
    for name in sorted(k for k in gt if not k.startswith("_")):
        img = cv2.imread(str(BASE / "samples" / f"{name}.PNG"))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        for i, box in enumerate(cells(img)):
            truth = gt[name]["counts"][i]
            med, p95, frac = art_features(hsv, box)
            b = badge_blob(hsv, box)
            bs = f"{b[0]:5.2f} {b[1]:5.2f} {b[2]:5.2f}" if b else "    -     -     -"
            print(f"{name:12s} {i:3d} {truth:4d} {med:6.0f} {p95:5.0f} {frac:6.1%} {bs}")


if __name__ == "__main__":
    main()
