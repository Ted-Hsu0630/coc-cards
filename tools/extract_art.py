"""萃取 60 張卡的卡面模板，只用在「視窗二選一」時當裁判。

平常辨識**完全不需要**這些模板 —— 邊框顏色的排列就唯一決定了視窗位置。
只有「整片聖水」那個視窗有兩個候選（相簿第 1~12 張 vs 第 7~18 張），
顏色分不出來，才需要看卡面。

模板轉灰階並做零均值正規化，這樣「別人擁有但我沒擁有」（彩色 vs 灰階）
造成的整體亮度差不會影響比對。

跑法：
    .venv/bin/python tools/extract_art.py
"""

import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import recognize as R

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "assets" / "art"


def main():
    gt = json.loads((BASE / "tools" / "groundtruth.json").read_text(encoding="utf-8"))
    ids = R.album_ids()
    OUT.mkdir(parents=True, exist_ok=True)

    # 只用同一位玩家、且完整涵蓋 60 張的那一組。混入別的玩家會讓同一張卡
    # 被不同收藏狀態（彩色／灰階）的模板覆蓋，而且覆蓋順序取決於檔名。
    whole = [n for n in gt if not n.startswith("_") and gt[n].get("player") == "A"]

    n = 0
    for name in sorted(whole):
        img = cv2.imread(str(BASE / "samples" / f"{name}.PNG"))
        start = gt[name]["start"]
        for i, (box, clip) in enumerate(R.grid(img)):
            if clip:
                continue          # 被切到的格子做不出完整的卡面模板
            patch = R.art_patch(img, box)
            if patch is None:
                print(f"  ⚠ {name}[{i}] 切不出卡面")
                continue
            cv2.imwrite(str(OUT / f"{ids[start + i]}.png"), patch)
            n += 1
    print(f"  {n} 張卡面模板 → {OUT}")


if __name__ == "__main__":
    main()
