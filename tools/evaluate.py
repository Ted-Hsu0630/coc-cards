"""端到端準確率評估，對 groundtruth.json 算分。

數字模板採**留一交叉驗證**：評估某張截圖時，模板只從其他截圖建立。
不這樣做的話等於拿訓練資料當考題，分數沒有意義。

ground truth 裡的 `null` 代表「這格被切太多，期望程式回報認不出」。
把它讀成任何具體數字都算**讀錯**，因為那是在猜。

跑法：
    .venv/bin/python tools/evaluate.py            # 原始解析度
    .venv/bin/python tools/evaluate.py --scale 0.5  # 降採樣模擬低解析度
"""

import argparse
import json
from collections import defaultdict
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from services import recognize as R

BASE = Path(__file__).resolve().parent.parent


def files(gt):
    return sorted(k for k in gt if not k.startswith("_"))


def sample(name):
    """samples/ 底下 iPhone 組是 .PNG、iPad 組是 .JPG。"""
    for ext in (".PNG", ".JPG"):
        p = BASE / "samples" / f"{name}{ext}"
        if p.exists():
            return p
    raise FileNotFoundError(f"samples/{name}.(PNG|JPG)")


def digits_excluding(gt, skip: str) -> dict[int, np.ndarray]:
    """用除了 skip 以外的截圖建數字模板。"""
    buckets = defaultdict(list)
    for name in files(gt):
        if name == skip:
            continue
        img = cv2.imread(str(sample(name)))
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
                buckets[int(ch)].append(
                    cv2.resize(bm, (24, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
                )
    return {d: np.mean(v, axis=0) for d, v in buckets.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0, help="先把截圖縮放這個倍率再辨識")
    args = ap.parse_args()

    gt = json.loads((BASE / "tools" / "groundtruth.json").read_text(encoding="utf-8"))
    ids = R.album_ids()
    names = files(gt)

    tot = defaultdict(int)
    problems = []

    # 逐張辨識，再**依玩家分組**用「視窗不重疊」定位。
    # 跨玩家不可以合併：不同人的相簿視窗當然會重疊，混在一起會互相干擾。
    # art={} 是刻意的：卡面模板正是從這些截圖萃取的，拿來測自己等於作弊。
    images, raw = {}, {}
    for name in names:
        img = cv2.imread(str(sample(name)))
        if args.scale != 1.0:
            img = cv2.resize(img, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_AREA)
        images[name] = img
        raw[name] = R.recognize(img, digits=digits_excluding(gt, skip=name), art={})

    resolved = {}
    by_player = defaultdict(list)
    for name in names:
        by_player[gt[name].get("player", name)].append(name)
    for group in by_player.values():
        out = R.resolve_batch([raw[n] for n in group], images=[images[n] for n in group], art=None)
        resolved.update(dict(zip(group, out, strict=True)))

    for name in names:
        img = images[name]
        r = resolved[name]
        start, hit, tied, cells = r.start, r.hit, len(r.tied), r.cells
        truth = gt[name]["counts"]
        want = gt[name]["start"]

        print(f"\n=== {name}  ({img.shape[1]}x{img.shape[0]})  玩家 {gt[name].get('player', '?')} ===")
        if not r.ok:
            print(f"  ✗ 不採用：{r.reason}")
            problems.append(f"{name}: 被拒絕（{r.reason}）")
            tot["file_fail"] += 1
            continue

        if len(cells) != len(truth):
            print(f"  ✗ 抓到 {len(cells)} 格，實際應有 {len(truth)} 格")
            problems.append(f"{name}: 格數不符（{len(cells)} vs {len(truth)}）—— 有格子被靜默丟掉")
            tot["file_fail"] += 1
            continue

        tot["cells"] += len(cells)
        ok_window = start == want
        tot["window_ok"] += int(ok_window)
        how = "顏色唯一" if tied == 1 else f"顏色有 {tied} 個候選，靠不重疊化解"
        clipped = sum(1 for c in cells if c.clip)
        cut = f"，{clipped} 格被切" if clipped else ""
        print(f"  {len(cells)} 格{cut}　起點 #{start}（正解 #{want}）吻合 {hit}/{len(cells)}  "
              f"{how}  {'✓' if ok_window else '✗ 位置錯'}")
        if not ok_window:
            problems.append(f"{name}: 視窗位置錯，讀成 #{start} 實際 #{want}")

        for i, c in enumerate(cells):
            t = truth[i]
            if t is None:
                # 期望拒答。猜了就是讀錯，不管猜得對不對
                if c.count is None:
                    tot["refuse_ok"] += 1
                else:
                    tot["count_wrong"] += 1
                    problems.append(f"{name}[{i}] 該拒答卻讀成 {c.count}（這格被切太多）")
                continue

            tot["known"] += 1

            if c.owned == (t > 0):
                tot["owned_ok"] += 1
            elif c.count is None:
                tot["owned_unknown"] += 1
            else:
                problems.append(f"{name}[{i}] 擁有判定錯：讀成 {c.owned}，實際 {t}")

            if c.count == t:
                tot["count_ok"] += 1
            elif c.count is None:
                tot["count_unknown"] += 1
                problems.append(f"{name}[{i}] 張數認不出來（實際 {t}）")
            else:
                tot["count_wrong"] += 1
                problems.append(f"{name}[{i}] 張數錯：讀成 {c.count}，實際 {t}")

        got = sum(1 for c in cells if c.count is not None)
        if ok_window:
            print(f"  讀出 {got}/{len(cells)} 張（{ids[start]} … {ids[start + len(cells) - 1]}）")

    n = tot["cells"]
    known = tot["known"]        # 有明確答案的格子（不含期望拒答的）
    print("\n" + "=" * 62)
    print(f"截圖 {len(names)} 張，格子 {n} 個（其中 {n - known} 個期望拒答）")
    print(f"  整張失敗   {tot['file_fail']}")
    print(f"  視窗定位   {tot['window_ok']}/{len(names) - tot['file_fail']}")
    print(f"  擁有判定   {tot['owned_ok']}/{known}"
          f"  (認不出 {tot['owned_unknown']})")
    print(f"  張數正確   {tot['count_ok']}/{known}")
    print(f"  該拒答有拒 {tot['refuse_ok']}")
    print(f"  張數認不出 {tot['count_unknown']}  （標記為人工確認，不會寫錯資料）")
    print(f"  張數讀錯   {tot['count_wrong']}  ← 這個才是真正危險的")
    if problems:
        print("\n問題明細：")
        for p in problems:
            print(f"  - {p}")


if __name__ == "__main__":
    main()
