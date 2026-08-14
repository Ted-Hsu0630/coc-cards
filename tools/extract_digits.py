"""從 samples 萃取徽章數字的模板，用 groundtruth.json 當標籤。

只萃得出 samples 裡出現過的數字（目前是 2、3、4）。沒有模板的數字
辨識時會回 None 並標記「要人工確認」—— 刻意不猜。
之後成員上傳的截圖若出現新數字，可以用同樣的方式補進來。

跑法：
    .venv/bin/python tools/extract_digits.py
"""

import json
from collections import defaultdict
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import recognize as R

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "assets" / "digits"


def main():
    gt = json.loads((BASE / "tools" / "groundtruth.json").read_text(encoding="utf-8"))
    buckets = defaultdict(list)

    for name in sorted(k for k in gt if not k.startswith("_")):
        img = cv2.imread(str(BASE / "samples" / f"{name}.PNG"))
        boxes = R.grid(img)
        for i, (box, _clip) in enumerate(boxes):
            truth = gt[name]["counts"][i]
            if truth is None or truth < 2:
                continue
            g = R.badge_glyphs(img, box)
            if g is None:
                print(f"  ⚠ {name}[{i}] 真實 x{truth} 但切不出字形")
                continue
            _, digit_parts = g
            if len(digit_parts) != len(str(truth)):
                print(f"  ⚠ {name}[{i}] 真實 x{truth} 但切出 {len(digit_parts)} 個數字")
                continue
            for ch, (_, _, _, bitmap) in zip(str(truth), digit_parts, strict=True):
                buckets[int(ch)].append(
                    cv2.resize(bitmap, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
                )

    OUT.mkdir(parents=True, exist_ok=True)
    for d, imgs in sorted(buckets.items()):
        # 多個樣本取平均，壓掉單張的雜訊
        avg = np.mean(imgs, axis=0)
        cv2.imwrite(str(OUT / f"{d}.png"), avg.astype(np.uint8))
        print(f"  數字 {d}：{len(imgs)} 個樣本 → {OUT / f'{d}.png'}")

    missing = [d for d in range(10) if d not in buckets]
    print(f"\n沒有模板的數字：{missing} —— 遇到時會標記為要人工確認")


if __name__ == "__main__":
    main()
