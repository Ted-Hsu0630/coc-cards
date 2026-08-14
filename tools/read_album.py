"""讀相簿截圖，印出每張卡的張數。

跑法：
    .venv/bin/python tools/read_album.py 某張.PNG 另一張.PNG
"""

import argparse
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import recognize as R

BASE = Path(__file__).resolve().parent.parent


def card_names():
    data = json.loads((BASE / "assets" / "cards.json").read_text(encoding="utf-8"))
    return {c["id"]: c["name_zh"] for c in data["cards"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--verbose", action="store_true", help="印出每格的判別中間值")
    args = ap.parse_args()

    names = card_names()
    ids = R.album_ids()
    digits, art = R.load_digits(), R.load_art()

    images, raw, kept = [], [], []
    for p in args.paths:
        img = cv2.imread(p)
        if img is None:
            print(f"{p}: 讀不到")
            continue
        r = R.recognize(img, digits=digits, art=art)
        if not r.ok:
            print(f"{Path(p).name}: 不採用 —— {r.reason}")
            continue
        images.append(img)
        raw.append(r)
        kept.append(p)

    if not raw:
        return
    resolved = R.resolve_batch(raw, images=images, art=art)

    merged, unknown = {}, []
    for p, img, r in zip(kept, images, resolved, strict=True):
        start, hit, tied, cells = r.start, r.hit, len(r.tied), r.cells
        print(f"\n=== {Path(p).name}  {img.shape[1]}x{img.shape[0]} ===")
        how = "顏色唯一" if tied == 1 else f"顏色有 {tied} 個候選"
        clipped = [c.clip for c in cells if c.clip]
        cut = f"，其中 {len(clipped)} 格被切（{'／'.join(sorted(set(clipped)))}）" if clipped else ""
        print(f"  {len(cells)} 格{cut}，起點 #{start}（相簿第 {start + 1}~{start + len(cells)} 張）"
              f"  吻合 {hit}/{len(cells)}  {how}")
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        for i, c in enumerate(cells):
            cid = ids[start + i] if start + i < len(ids) else "?"
            label = f"{cid} {names.get(cid, '?')}"
            if c.count is None:
                unknown.append(label)
                shown = "認不出"
            else:
                merged[cid] = c.count
                shown = f"{c.count} 張" if c.count else "未擁有"
            if args.verbose:
                h_full = max(x.box[3] for x in cells)
                _, p95 = R.cell_owned(hsv, c.box, c.clip, h_full)
                intact = "-" if c.clip == "bottom" else f"{R.border_intact(hsv, c.box, c.series)[1]:.2f}"
                print(f"    {label:24s} {shown:6s}  切={c.clip or '無':4s} "
                      f"S95={p95:5.0f} 底邊={intact:>4s} {c.note}")
            else:
                print(f"    {label:24s} {shown}")

    print(f"\n讀出 {len(merged)} 張，認不出 {len(unknown)} 張")
    if unknown:
        print("  要人工確認：" + "、".join(unknown))

    # 有讀到完整系列的話，跟遊戲內進度條對一下當檢查碼
    data = json.loads((BASE / "assets" / "cards.json").read_text(encoding="utf-8"))
    i = 0
    for s in data["series"]:
        seg = ids[i:i + s["count"]]
        got = [c for c in seg if c in merged]
        if len(got) == s["count"]:
            owned = sum(1 for c in seg if merged[c] > 0)
            print(f"  {s['name_zh']}：{owned}/{s['count']}  ← 跟遊戲內進度條核對這個數字")
        i += s["count"]


if __name__ == "__main__":
    main()
