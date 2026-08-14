"""萃取數字模板（0~9 與斜線），來源有三個。

**進度條是主力來源。** 徽章只出現過 2、3、4 —— 22 張截圖裡就是沒人囤到 5 張
以上同一張卡。但畫面上方的系列進度條用的是同一套字體，1~9 全都有，
而且**答案就寫在旁邊**（`14/19` 本身就是標籤），是自帶 ground truth 的資料。

實測：純用進度條萃取的模板去讀那 64 個徽章，64/64 全對、零認不出、零讀錯。
字體轉移沒有問題，所以兩個來源混在一起平均。

`0` 只有「已收集卡牌：N/60」那行有（進度條的分母是 19/13/11/17，
分子也沒人剛好 10）。那行旁邊有中文字，自動定位不穩，所以用固定裁切 ——
這是離線的一次性工具不是執行期程式碼，寫死座標可以接受，
而且切出來的字形數量會當場對照預期字串，不符就跳過不硬塞。

跑法：
    .venv/bin/python tools/extract_digits.py
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import progress as P  # noqa: E402
from services import recognize as R  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "assets" / "digits"

# 「已收集卡牌：40/60」裡的那串，只為了取 0。
# 座標是 IMG_4926（2622x1206）專用，換張圖就不對 —— 見模組說明。
ZERO_SOURCE = ("IMG_4926", (925, 1000, 1320, 1500), "40/60")


def _norm(bitmap):
    return cv2.resize(bitmap, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)


def sample_path(name):
    for ext in (".PNG", ".JPG"):
        p = BASE / "samples" / f"{name}{ext}"
        if p.exists():
            return p
    raise FileNotFoundError(f"samples/{name}.(PNG|JPG)")


def from_bars(gt, buckets):
    """進度條：1~9 與斜線。"""
    bad = 0
    for name in sorted(k for k in gt if not k.startswith("_")):
        img = cv2.imread(str(sample_path(name)))
        found = P.find_bars(img)
        for key, pair in gt["_bars"][gt[name]["player"]].items():
            if pair is None or key not in found:
                continue
            want = f"{pair[0]}/{pair[1]}"
            glyphs = P.bar_glyphs(img, found[key])
            if len(glyphs) != len(want):
                print(f"  ⚠ {name} {key}：期望 {want}（{len(want)} 字）但切出 {len(glyphs)} 個")
                bad += 1
                continue
            for ch, g in zip(want, glyphs, strict=True):
                buckets[ch].append(_norm(g["bitmap"]))
    return bad


def from_badges(gt, buckets):
    """徽章：2、3、4。"""
    for name in sorted(k for k in gt if not k.startswith("_")):
        img = cv2.imread(str(sample_path(name)))
        for i, (box, _clip) in enumerate(R.grid(img)):
            truth = gt[name]["counts"][i]
            if truth is None or truth < 2:
                continue
            g = R.badge_glyphs(img, box)
            if g is None:
                continue
            _, parts = g
            if len(parts) != len(str(truth)):
                continue
            for ch, (_, _, _, bm) in zip(str(truth), parts, strict=True):
                buckets[ch].append(_norm(bm))


def from_collected(buckets):
    """「已收集卡牌：40/60」：只為了取 0。"""
    name, (y0, y1, x0, x1), want = ZERO_SOURCE
    img = cv2.imread(str(sample_path(name)))
    roi = img[y0:y1, x0:x1]
    white = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), P.WHITE_LO, P.WHITE_HI)
    n, lab, st, _ = cv2.connectedComponentsWithStats(white, 8)
    raw = []
    for i in range(1, n):
        L, T = int(st[i, cv2.CC_STAT_LEFT]), int(st[i, cv2.CC_STAT_TOP])
        w, h = int(st[i, cv2.CC_STAT_WIDTH]), int(st[i, cv2.CC_STAT_HEIGHT])
        if w > h * 1.4:
            continue
        raw.append((L, h, (lab[T:T + h, L:L + w] == i).astype(np.uint8) * 255))
    tallest = max((r[1] for r in raw), default=0)
    keep = sorted((r for r in raw if r[1] >= tallest * 0.5), key=lambda r: r[0])
    if len(keep) != len(want):
        print(f"  ⚠ {name} 的「{want}」切出 {len(keep)} 個字形，跳過（0 會缺）")
        return
    for ch, (_, _, bm) in zip(want, keep, strict=True):
        buckets[ch].append(_norm(bm))


def main():
    gt = json.loads((BASE / "tools" / "groundtruth.json").read_text(encoding="utf-8"))
    buckets = defaultdict(list)

    bad = from_bars(gt, buckets)
    from_badges(gt, buckets)
    from_collected(buckets)

    OUT.mkdir(parents=True, exist_ok=True)
    for ch, imgs in sorted(buckets.items()):
        # 多個樣本取平均，壓掉單張的壓縮雜訊與抗鋸齒差異
        avg = np.mean(imgs, axis=0).astype(np.uint8)
        cv2.imwrite(str(OUT / ("slash.png" if ch == "/" else f"{ch}.png")), avg)
        print(f"  '{ch}'：{len(imgs):3d} 個樣本")

    missing = [d for d in "0123456789" if d not in buckets]
    print(f"\n  切壞的進度條：{bad}")
    print(f"  缺模板的數字：{missing or '無'}")


if __name__ == "__main__":
    main()
