# coc-cards — 部落衝突「卡牌衝突」換卡配對站

[![CI](https://github.com/Ted-Hsu0630/coc-cards/actions/workflows/ci.yml/badge.svg)](https://github.com/Ted-Hsu0630/coc-cards/actions/workflows/ci.yml)

部落成員各自登記自己的 60 格卡牌收藏，網站自動算出**跟誰換、換哪張**。

登入靠遊戲內的 API 權杖驗證，所以沒有密碼、也不會有人冒充別人登記。
也可以直接上傳相簿截圖，程式讀出張數再讓你核對。

| | |
|---|---|
| 交換規則、卡表結構、API 實測行為 | **[SPEC.md](SPEC.md)** |
| 截圖辨識怎麼做的、為什麼那樣做 | **[tools/FINDINGS.md](tools/FINDINGS.md)** |
| 動手前必讀的紅線 | **[CLAUDE.md](CLAUDE.md)** |

---

## 用 Docker 跑（最快）

```bash
cp .env.example .env
```

編輯 `.env` 填入 `COC_API_KEY`（下面說怎麼拿）。本機用 http 的話還要加一行
`COOKIE_SECURE=0`，否則瀏覽器會把 cookie 丟掉，登入會變成「成功但沒登入」。

```bash
docker compose up -d
```

開 <http://localhost:3848>。

```bash
docker compose logs -f coc-cards    # 看 log
docker compose down                 # 停掉（資料庫在 volume 裡，不會消失）
```

映像檔含 OpenCV，第一次 build 大概兩三分鐘。

## 不用 Docker 跑

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt -r requirements-cv.txt
cp .env.example .env      # 填入 COC_API_KEY，並加上 COOKIE_SECURE=0
.venv/bin/python main.py
```

`requirements-cv.txt` 是截圖辨識用的 OpenCV。不裝也能跑，只是「截圖匯入」
分頁會自動隱藏（`/api/import/available` 會回 `false`）。

測試：

```bash
.venv/bin/python -m pytest -q && .venv/bin/ruff check .
```

## CoC API key

到 [developer.clashofclans.com](https://developer.clashofclans.com) 免費申請。

**金鑰綁定你建立時填的來源 IP。** IP 一變，所有查詢都回 403 —— 而且跟「金鑰無效」
是同一個狀態碼，很容易誤判。`services/coc.py` 會把這種情況翻譯成看得懂的訊息。

## 部署到自己的機器

`docker-compose.yml` 是可以獨立跑的通用版本。**每台機器不一樣的東西不要改它** ——
放 `docker-compose.override.yml`，Compose 會自動疊上去，而且那個檔名在 `.gitignore`，
`git pull` 不會覆蓋掉。

最常見的需求是把容器接到反向代理所在的 Docker 網路：

```bash
cp docker-compose.override.example.yml docker-compose.override.yml
# 編輯成你的網路名稱
docker compose config      # 確認合併後的結果符合預期
docker compose up -d
```

更新用 `./update.sh`：

```bash
./update.sh
```

它會 `git pull --ff-only` → build → 起容器 → **實際打一次 `/healthz`** 才算成功。
只看容器有沒有起來是不夠的：API key 失效時容器活得好好的，但功能全壞。

## 結構

```
assets/cards.json    卡表（60 張）。名字打錯不影響配對，改這裡即可
assets/digits/       徽章數字模板（截圖辨識用）
assets/art/          卡面模板（只在視窗二選一時當裁判）
assets/icon-master.png  PWA 圖示的裁切母圖（tools/make_icons.py 用）
data/                Docker volume，只放資料庫 —— 靜態檔案放這裡不會生效

core/cards.py        卡表載入 + 啟動時的結構驗證
core/tags.py         村莊標籤正規化（# 沒編碼會得到 404，見 SPEC §8）
core/db.py           SQLite schema 與連線
services/coc.py      CoC API 客戶端（async、並發 10）
services/auth.py     權杖驗證、session
services/players.py  村莊、收藏、部落同步
services/matching.py 配對演算法（純函式，不碰 DB）
services/recognize.py 截圖辨識
services/importer.py  多張截圖合併成待確認的收藏
routers/             HTTP 端點
web/                 前端（原生 HTML/CSS/JS，無框架、無 build step）
tests/               規則與紅線的回歸測試
tools/               本機用的分析腳本，不進映像檔
samples/             辨識測資
```

## 關於截圖辨識

不靠卡面美術辨識是哪張卡。相簿是連續 6 欄、沒有水平捲動，所以一張截圖必定是
固定序列的連續視窗，起點是 6 的倍數 —— 60 張卡只有 9 個候選位置。
光靠邊框顏色的排列，9 個裡就有 8 個是唯一解。

實測 22 張截圖、6 位玩家、兩種裝置（iPhone 的 PNG 與 iPad 經通訊軟體壓縮的 JPG，
長寬比 2.17 對 1.52）：270 格裡讀對 263 格、拒答 6 格、**讀錯 0 格**。

辨識結果**不會直接寫進資料庫**，一律先讓使用者核對。讀不出來的格子會留白並
說明原因，不會猜一個數字給你。非相簿的圖片會被擋下來並說明為什麼。

過程中推翻了自己三個看起來很漂亮的結論，理由都寫在
[tools/FINDINGS.md](tools/FINDINGS.md)。

## 加到主畫面

`web/manifest.webmanifest` 讓網站可以加到手機主畫面，開起來沒有網址列
（`display: standalone`）。**刻意不做 Service Worker** —— 這個站的資料是即時的，
離線打開等於沒用；代價是 Android Chrome 不會主動跳安裝橫幅，要從選單手動加。

圖示由 [tools/make_icons.py](tools/make_icons.py) 從 `assets/icon-master.png`
產生，構圖參數在那支腳本裡。

## 美術素材

卡面、活動主視覺與圖示取自《部落衝突》遊戲畫面，版權屬 Supercell。
本專案為非商業的同人工具，依 [Supercell Fan Content Policy](https://supercell.com/en/fan-content-policy/)
使用，與 Supercell 無關，也未經其背書。
