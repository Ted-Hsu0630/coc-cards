# coc-cards — 部落衝突「卡牌衝突」換卡配對站

部落成員各自登記收藏，網站自動算出**跟誰換、換哪張**。

活動 2026-08-01 ~ 08-31，交換功能到 09-02 08:00 UTC。

完整規格（交換規則、卡表結構、辨識參數、API 行為）在 **[SPEC.md](SPEC.md)**，
改任何東西之前先讀那份。這份 README 只講怎麼跑。

## 現況

- **階段一 已完成**：權杖驗證登入、一人多帳號、60 格收藏表、兩兩配對、
  部落顯示與過濾、部落總覽
- **階段二 未開始**：上傳截圖自動辨識收藏（可行性已驗證，見 SPEC §7）

## 本機開發

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env      # 填入 COC_API_KEY
echo "COOKIE_SECURE=0" >> .env   # localhost 是 http，不關掉瀏覽器會丟掉 cookie
.venv/bin/python main.py
```

開 http://localhost:3848

```bash
.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check .
```

## CoC API key

到 [developer.clashofclans.com](https://developer.clashofclans.com) 建立，
**金鑰綁定建立時填的來源 IP**。本機開發與伺服器共用同一個公網 IP，所以一把就夠。

IP 一變所有查詢會回 403（跟「金鑰無效」同一個狀態碼），
`services/coc.py` 會把這種情況翻譯成看得懂的訊息。

## 部署

接 infra 的 `edge` 網路，反向代理設定在 `infra/conf/Caddyfile` 的 `coc-cards.592355.xyz`
（橘雲 + `import cloudflare_only`）。

```bash
git push origin main
```

```bash
ssh server 'cd ~/coc-cards && ./update.sh'
```

`update.sh` 會 build → 起容器 → **實際打一次 `/healthz`** 才算成功。
容器起得來但 API key 壞掉的情況會在這一步被抓到。

新增站台的完整流程（Cloudflare 子網域、Caddyfile）見 `infra/README.md`。

## 結構

```
assets/cards.json    卡表（60 張）。名字打錯不影響配對，改這裡即可
assets/digits/       徽章數字模板（截圖辨識用）
assets/art/          卡面模板（只在視窗二選一時當裁判）
data/                Docker named volume，只放資料庫 —— 靜態檔案放這裡不會生效
core/cards.py        卡表載入 + 啟動時的結構驗證
core/tags.py         村莊標籤正規化（# 沒編碼會得到 404，見 SPEC §8）
core/db.py           SQLite schema 與連線
services/coc.py      CoC API 客戶端（async、並發 10）
services/auth.py     權杖驗證、session
services/players.py  村莊、收藏、部落同步
services/matching.py 配對演算法（純函式，不碰 DB）
routers/             HTTP 端點
web/                 前端（原生 HTML/CSS/JS，無框架）
tests/               規則與紅線的回歸測試
```

## Port

3848。3847 camera-viewer、8000 autocare、8555 go2rtc 已佔用（見 `infra/README.md`）。
