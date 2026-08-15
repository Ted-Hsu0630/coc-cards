"""從活動原圖產生 PWA 圖示。

原始截圖是隨手截的，人物偏左上、下半部全是桌面跟金幣 —— 那些東西縮到 48px
就只剩一片糊，所以這裡不是等比縮小，是**重新構圖**：裁掉桌面、把野蠻人的臉
移到該在的位置，再縮到各個尺寸。

兩種裁法是刻意分開的：

- `any` 會被原封不動地畫成正方形（瀏覽器分頁、Windows 開始選單），裁緊一點
  比較有力。
- `maskable` 會被系統套上自己的形狀（Android 可能是圓形、水滴、方形），
  規格保證看得到的只有**中心 80% 直徑的圓**，圓外隨時可能被切掉。所以這一版
  裁鬆、把頭跟拳頭整個塞進那個圓裡。只出 `any` 的話，Android 會自己加一圈
  白底縮圖，臉會變得很小。

原圖高度不夠讓 maskable 把頭擺進安全圓 —— 頭頂只離上緣 45px。所以 master
先在上方補了一段：把最上面幾列**鏡射**再模糊，接縫那一列跟原圖第 0 列完全
相同，所以看不出接痕（直接拉伸最上一列會出現一條明顯的水平分界，試過了）。
補好的圖存成 assets/icon-master.png，這支腳本只吃它，不再碰原始截圖。

用 cv2 而不是 Pillow：Pillow 不在這專案的相依清單裡，CI 裝不到。
改構圖就改下面的 ANY / MASKABLE 常數，然後重跑：

    python tools/make_icons.py
"""

import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "assets" / "icon-master.png"
OUT = ROOT / "web" / "img"

# (中心 x, 中心 y, 邊長)，master 座標。master 上緣補了 PAD 高度，
# 所以 y 比原始截圖大 PAD。
PAD = 90
ANY = (290, 212 + PAD, 424)
MASKABLE = (275, 168 + PAD, 460)

# 每個尺寸都從 master 直接縮，不要拿 512 再縮 —— 那等於縮兩次，小尺寸會糊。
TARGETS = [
    ("icon-192.png", ANY, 192),
    ("icon-512.png", ANY, 512),
    ("icon-maskable-192.png", MASKABLE, 192),
    ("icon-maskable-512.png", MASKABLE, 512),
    # iOS 不吃 maskable，會自己切圓角，所以用 any 那一版。
    ("apple-touch-icon.png", ANY, 180),
    ("favicon-32.png", ANY, 32),
]


def main() -> int:
    # IMREAD_COLOR 會丟掉 alpha，這正是我們要的：maskable 的透明處
    # 會被系統填成白色，圖示一律不能有透明通道。
    master = cv2.imread(str(MASTER), cv2.IMREAD_COLOR)
    if master is None:
        print(f"讀不到 {MASTER}", file=sys.stderr)
        return 1

    h, w = master.shape[:2]
    for name, (cx, cy, s), size in TARGETS:
        x0, y0 = cx - s // 2, cy - s // 2
        if x0 < 0 or y0 < 0 or x0 + s > w or y0 + s > h:
            print(f"{name}: 裁切範圍超出 master {w}x{h}", file=sys.stderr)
            return 1
        crop = master[y0 : y0 + s, x0 : x0 + s]
        # 縮小用 INTER_AREA（會把整個區域平均掉，不會出摩爾紋），
        # 放大用 LANCZOS4。用錯方向的話小尺寸會出現鋸齒狀的雜訊。
        interp = cv2.INTER_AREA if size < s else cv2.INTER_LANCZOS4
        img = cv2.resize(crop, (size, size), interpolation=interp)
        cv2.imwrite(str(OUT / name), img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
        print(f"{name}  {size}x{size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
