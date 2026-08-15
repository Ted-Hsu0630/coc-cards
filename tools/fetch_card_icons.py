"""抓 60 張卡的頭像圖，存成 web/img/cards/<我們的卡片 id>.png。

圖來自 clash.ninja 的 /images/entities/<entity_id>_icon.png（底層是 Supercell
的遊戲素材，那站跟我們一樣是依 Fan Content Policy 使用）。**抓下來自架，
不要直接連他們的網址** —— 熱連會讓每個使用者每次開頁面都在耗他們的流量，
而且他們改檔名或擋 referer 的話我們整片圖會一起空掉。

檔名用**我們的**卡片 id，所以前端只要 `/static/img/cards/${c.id}.png`，
執行期不需要任何對照表。對照只存在這支腳本裡。

對照是**按身分對，不是按位置對**。他們的相簿順序跟遊戲內不同（超級部隊排在
建築大師前面），照 data-card-number 抄會整段錯位。名字也有兩處對不上，
明寫在 ALIAS 的註解裡而不是靠模糊比對。

跑法（一次性，圖不會變）：
    python tools/fetch_card_icons.py
"""

import json
import sys
import time
from pathlib import Path

# 用 httpx 不用 urllib：urllib 走系統的 CA，而 python.org 版的 Python 在 macOS
# 上沒接上鑰匙圈，會直接 CERTIFICATE_VERIFY_FAILED。httpx 自帶 certifi，
# 而且本來就在 requirements.txt 裡。
import httpx

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "web" / "img" / "cards"
URL = "https://www.clash.ninja/images/entities/{eid}_icon.png"

# 我們的卡片 id -> clash.ninja 的 entity id。
# 兩處名字對不上，是人工核對過的：
#   dark-13     我們 Ruin Witch   / 他們 Rubble Witch (282)
#   builder-06  我們 Baby Dragon（跟 elixir-11 撞名）/ 他們 BB Baby Dragon (95)
ENTITY = {
    "elixir-01": 31, "elixir-02": 32, "elixir-03": 33, "elixir-04": 34,
    "elixir-05": 35, "elixir-06": 36, "elixir-07": 37, "elixir-08": 38,
    "elixir-09": 39, "elixir-10": 40, "elixir-11": 41, "elixir-12": 42,
    "elixir-13": 103, "elixir-14": 121, "elixir-15": 133, "elixir-16": 138,
    "elixir-17": 156, "elixir-18": 204, "elixir-19": 241,
    "dark-01": 53, "dark-02": 54, "dark-03": 55, "dark-04": 56,
    "dark-05": 57, "dark-06": 58, "dark-07": 59, "dark-08": 111,
    "dark-09": 123, "dark-10": 151, "dark-11": 197, "dark-12": 218, "dark-13": 282,
    "builder-01": 90, "builder-02": 91, "builder-03": 92, "builder-04": 93,
    "builder-05": 94, "builder-06": 95, "builder-07": 96, "builder-08": 97,
    "builder-09": 98, "builder-10": 101, "builder-11": 113,
    "super-01": 177, "super-02": 178, "super-03": 179, "super-04": 180,
    "super-05": 181, "super-06": 182, "super-07": 183, "super-08": 184,
    "super-09": 185, "super-10": 191, "super-11": 221, "super-12": 186,
    "super-13": 192, "super-14": 187, "super-15": 188, "super-16": 189,
    "super-17": 190,
}


def main() -> int:
    cards = json.loads((BASE / "assets" / "cards.json").read_text(encoding="utf-8"))["cards"]
    ids = [c["id"] for c in cards]

    # 卡表加了新卡而這裡沒跟上的話，畫面會出現一格破圖而且沒有任何錯誤訊息。
    missing = [i for i in ids if i not in ENTITY]
    extra = [i for i in ENTITY if i not in ids]
    if missing or extra:
        print(f"對照表跟卡表對不上：缺 {missing}，多 {extra}", file=sys.stderr)
        return 1
    if len(set(ENTITY.values())) != len(ENTITY):
        print("有兩張卡對到同一個 entity id", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "coc-cards/1.0 (personal clan tool)"}
    with httpx.Client(headers=headers, timeout=20, follow_redirects=True) as client:
        for i, cid in enumerate(ids):
            dst = OUT / f"{cid}.png"
            if dst.exists():
                continue
            try:
                res = client.get(URL.format(eid=ENTITY[cid]))
                res.raise_for_status()
            except httpx.HTTPError as e:
                print(f"{cid}: 抓不到 —— {e}", file=sys.stderr)
                return 1
            if not res.content.startswith(b"\x89PNG"):
                print(f"{cid}: 回來的不是 PNG（{len(res.content)} bytes）", file=sys.stderr)
                return 1
            dst.write_bytes(res.content)
            print(f"{cid}  {len(res.content) // 1024} KB")
            if i + 1 < len(ids):
                time.sleep(0.3)  # 別把人家的站當自己的
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
