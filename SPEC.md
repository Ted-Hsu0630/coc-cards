# coc-cards — 部落衝突「卡牌衝突」換卡配對站

實作規格。動手前先讀完，**規則章節是配對正確性的唯一依據，改動前要先確認遊戲內行為**。

---

## 1. 背景

《部落衝突》2026 年 8 月「卡牌衝突（Clash of Cards）」活動：

- 活動期間 **2026-08-01 ~ 08-31**，交換功能延長到 **09-02 08:00 UTC** 截止
- 全遊戲兵種變成 **60 張**收集卡，集滿 60 張解鎖「漫畫版憤怒王子」造型
- 交換在**部落聊天室**內進行，雙方必須**同時在線**才能完成

本站要解決的問題：部落成員各自缺什麼、多什麼沒人知道，只能在聊天室裡瞎喊。
本站讓每個人登記自己的收藏，自動算出**跟誰換、換什麼**。

> 站台本身不碰遊戲內的交換動作，只做配對建議。實際交換仍要玩家自己在遊戲裡操作。

---

## 2. 交換規則（核心，不可寫錯）

### 2.1 單張卡的三種狀態

| 持有數 | 狀態 | 能換出 | 能換入 |
|---|---|---|---|
| 0 | 缺（missing） | ✗ | ✓ |
| 1 | 剛好（exact） | ✗ 最後一張不能送 | ✓ |
| ≥2 | 多（spare） | ✓ 可送出 `count − 1` 張 | ✓ |

### 2.2 一筆交換的完整條件

交換**一定有方向**：發起方 `I`（在部落聊天室貼出請求的人）、接收方 `R`（回應的人）。

```
I 送出 X    要求 I.count[X] ≥ 2      （只能送多餘的）
I 收到 Y    要求 I.count[Y] == 0     （只能指定自己沒有的）  ← 不對稱的關鍵
R 送出 Y    要求 R.count[Y] ≥ 2      （只能送多餘的）
R 收到 X    無限制                    （已擁有也照收）        ← 不對稱的關鍵
且          series(X) == series(Y)   （只能同系列互換）
```

**兩個不對稱點務必記牢：**

1. **發起方換入的卡必須是自己完全沒有的**（`count == 0`）；已經有 1 張以上的卡不能當成交換目標。
2. **接收方換入沒有任何限制** — 就算已經有 3 張，還是可以再收一張進來。

### 2.3 同系列限制

只能**同系列換同系列**（聖水↔聖水、闇黑↔闇黑、建築大師基地↔建築大師基地、超級部隊↔超級部隊）。
**已由使用者在遊戲內確認。** 跨系列一律不成立。

### 2.4 已知但本站不實作的規則

以下規則存在於遊戲中，但不影響配對建議，先不做：

- 請求有冷卻時間（類似部落城堡援軍），可用寶石跳過
- 商人（Trader）可用重複卡換卡包：一般卡 2 張、超級兵種卡 3 張（**待確認**）
- 雙方必須同時在線

---

## 3. 卡表

### 3.1 結構（已由截圖確認）

| 系列 | key | 張數 | 邊框色 | 全域索引 |
|---|---|---|---|---|
| 聖水卡牌 | `elixir` | 19 | 洋紅 magenta | 0–18 |
| 闇黑重油卡牌 | `dark` | 13 | 深紫 purple | 19–31 |
| 建築大師基地卡牌 | `builder` | 11 | 藍 blue | 32–42 |
| 超級部隊卡牌 | `super` | 17 | 橘 orange | 43–59 |
| **合計** | | **60** | | |

相簿是**連續 6 欄格狀、共 10 列**，系列依上表順序連續排列，中間不換行分隔。
全域索引 `i` 對應 `row = i // 6`、`col = i % 6`（皆 0-based）。

上述邊界已用 5 張截圖逐格核對過：`i=18` 落在第 4 列第 1 格（洋紅邊框，聖水最後一張）、
`i=19` 第 4 列第 2 格（紫框，闇黑第一張）、`i=32` 第 6 列第 3 格（藍框）、
`i=43` 第 8 列第 2 格（橘框）。全部吻合。

### 3.2 卡片 ID

用**位置**當 ID，不用名字 — 名字打錯不影響配對正確性：

```
elixir-01 … elixir-19
dark-01   … dark-13
builder-01 … builder-11
super-01  … super-17
```

存放於 `data/cards.json`，不進資料庫。之後遊戲加兵種只改這個檔。

```json
{
  "id": "elixir-01",
  "series": "elixir",
  "index": 0,
  "name_zh": "野蠻人",
  "name_en": "Barbarian",
  "confirmed": true
}
```

`confirmed: false` 代表名字是草稿、尚未跟遊戲畫面核對過。
**名字由開發者直接改程式碼，網頁上不留編輯入口**（使用者明確要求）。

### 3.3 名字來源

從截圖辨識 60 張卡的名字有困難（縮圖小、未擁有卡是灰階）。做法：
先填草稿並標 `confirmed: false`，使用者對照遊戲逐一確認後改成 `true`。
**配對邏輯完全不依賴名字**，所以名字錯不會造成配對錯誤，只是顯示不好看。

---

## 4. 身分與帳號

### 4.1 驗證流程（遊戲內 API 權杖）

1. 玩家在遊戲內：設定 → 更多設定 → API 權杖，取得 8 碼權杖
2. 前端送 `{ tag, token }` 到 `POST /api/players/verify`
3. 後端 `POST https://api.clashofclans.com/v1/players/{tag}/verifytoken`，body `{"token": "..."}`
4. 通過後 `GET /v1/players/{tag}` 取 `name`、`clan.tag`、`clan.name`
5. 寫入 `players`，發或延續 session

權杖是一次性的驗證憑據，**不儲存**。

### 🔴 紅線：verifytoken 失敗也回 HTTP 200

實測（2026-08-14）：

```
正確權杖 → HTTP 200  {"tag":"#9QRUL2CVJ","token":"abcd1234","status":"ok"}
錯誤權杖 → HTTP 200  {"tag":"#9QRUL2CVJ","token":"00000000","status":"invalid"}
```

**兩者的 HTTP 狀態碼一模一樣。** 只檢查 `resp.status_code == 200`
或用 `raise_for_status()` 就當作驗證通過的話，**任何人都能冒充任何玩家**，
整套身分驗證形同虛設。

必須檢查 **response body 的 `status` 欄位等於 `"ok"`**：

```python
data = resp.json()
if resp.status_code != 200 or data.get("status") != "ok":
    raise VerificationFailed
```

這條要有對應的單元測試（mock 一個 200 + `status: "invalid"` 的回應，
斷言驗證被拒絕），避免日後重構時退化。

### 4.2 一人多帳號

- 第一個驗證通過的村莊建立 `users` 列，成為主帳號
- 已登入狀態下再驗證其他 tag → 掛到同一個 `user_id`（每個小號都要各自過權杖驗證）
- session 綁 `user_id` **不綁 tag**；前端有「目前操作中的村莊」選擇器，切換不需重新登入
- 若某個 tag 已屬於別的 `user_id`，回 409 不覆蓋（避免搶帳號）

### 4.3 部落範圍

**不限部落** — 任何通過權杖驗證的玩家都能建表。
部落資訊的用途是**配對時的可行性判斷**，不是准入條件：

- `players.clan_tag` / `clan_name` 是快取，配對前依 `clan_synced_at` 決定要不要重抓
- 配對結果每一筆都標示對方目前所在部落
- 提供「**只顯示同部落**」開關（預設開啟 — 不同部落實際上換不了）

---

## 5. 資料模型（SQLite）

```sql
CREATE TABLE users (
  id          INTEGER PRIMARY KEY,
  created_at  TEXT NOT NULL
);

CREATE TABLE players (
  tag             TEXT PRIMARY KEY,   -- 正規化：大寫、含 #
  user_id         INTEGER NOT NULL REFERENCES users(id),
  name            TEXT NOT NULL,
  clan_tag        TEXT,               -- 無部落為 NULL
  clan_name       TEXT,
  clan_synced_at  TEXT,               -- ISO8601，配對前的重抓依據
  verified_at     TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
CREATE INDEX idx_players_user ON players(user_id);
CREATE INDEX idx_players_clan ON players(clan_tag);

CREATE TABLE collections (
  tag        TEXT NOT NULL REFERENCES players(tag),
  card_id    TEXT NOT NULL,
  count      INTEGER NOT NULL CHECK (count >= 0),
  PRIMARY KEY (tag, card_id)
);

CREATE TABLE sessions (
  token       TEXT PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id),
  expires_at  TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
```

`collections` 只存 `count > 0` 的列；查不到即視為 0。

---

## 6. 配對演算法

### 6.1 定義

對觀看者的當前村莊 `V`，以及資料庫中每個其他村莊 `O`（含 `V` 主人的其他小號）：

```
spare(P, C)    ⟺  P.count[C] ≥ 2
missing(P, C)  ⟺  P.count[C] == 0
```

### 6.2 三種配對結果

**互利互換（mutual）— 最優先**

存在同系列的 X、Y 使得：
```
spare(V, X) ∧ missing(O, X) ∧ missing(V, Y) ∧ spare(O, Y)
```
雙方各補一個空缺。誰當發起方都成立（條件對稱），UI 標「互換」。

**我受益（incoming）**

```
missing(V, Y) ∧ spare(O, Y) ∧ ∃X: series(X) == series(Y) ∧ spare(V, X)
```
且不滿足互利條件（也就是找不到任何這樣的 X 讓 `missing(O, X)` 成立）。
此時 **V 必須當發起方**（因為只有發起方能指定換入自己沒有的卡）。
O 收下一張自己已有的 X，純粹幫忙。

**我幫人（outgoing）**

```
missing(O, X) ∧ spare(V, X) ∧ ∃Y: series(Y) == series(X) ∧ spare(O, Y)
```
且不滿足互利條件。此時 **O 當發起方**，V 收下一張可能已有的 Y。

### 6.3 輸出與排序

依對象分組，每個對象一張卡片，內容：
對方暱稱、目前部落、是否同部落、互利/我受益/我幫人、具體哪幾張換哪幾張。

排序：`互利` → `我受益` → `我幫人`；同類別內按「可成立的組合數」由多到少。
「只顯示同部落」開關開啟時，非同部落的對象整個過濾掉。

### 6.4 不做的事

**不做全域最佳分配、不做多步驟鏈**（使用者明確選擇「只列兩兩配對」）。
同一張多餘卡會同時出現在對多個人的建議中 — 這是刻意的，實際換掉哪張由玩家自己決定。

---

## 7. 截圖辨識（階段二）

### 7.1 可用訊號

關鍵觀察：**未擁有的卡，邊框仍是彩色的，只有卡面美術變灰階。**

| 訊號 | 位置 | 得到 |
|---|---|---|
| 邊框顏色 | 卡片外框 | 系列 → 比對範圍從 60 縮到 11~19 |
| 卡面飽和度 | 卡片中央 | 是否擁有（灰＝0 張） |
| 黃色徽章 | 卡片底部中央 | `x2` 起的張數；彩色且無徽章＝1 張。**已實測到 `x4`，上限未知，要能處理兩位數** |
| 卡面灰階結構 | 卡片中央 | 是哪一張（模板比對） |

### 7.2 流程

1. HSV 遮罩取出四個系列的邊框色（高飽和 + 特定 hue 區間），閉運算把空心框補實，抓輪廓
2. **先**用長寬比過濾（實測 **0.77~0.90**，非 0.72），**再**用面積中位數收斂。
   順序不能顛倒 — 頂部的系列進度條長寬比 3.85、面積跟卡片相近，
   先算面積中位數會被它拉歪，導致一格都抓不到。
3. **輪廓不可直接當成格子** ⚠️ — 同系列的相鄰卡片邊框同色，閉運算會把它們橋接成
   一整塊，實測 12 格只抓到 6~7 格（混色的截圖才剛好 12 格）。
   正確做法：**用抓到的輪廓推出格線間距與相位，再擬合規則格網（6 欄）補回所有格子。**
   實測間距完全均勻（x = 535, 808, 1081, 1354, 1626, 1899，pitch 273±1），擬合很穩。
4. **不使用絕對座標**（使用者明確要求：不同螢幕比例格子位置會變）
5. 每格縮放到固定尺寸（64×64）、轉灰階 → 與 60 張模板做正規化互相關比對
6. 邊框色先決定系列，只在該系列的模板內比對
7. 徽章區域偵測黃色色塊，有的話切出數字部分用模板比對出 N。
   **不可假設只有個位數** — 要先切分連通元件再逐字辨識，才吃得下 `x10` 這種兩位數。
8. 結果進**人工確認頁**，使用者核對後才寫入資料庫（不直接覆蓋）

### 7.2.1 實測參數（sample 2622×1206）

| 項目 | 值 |
|---|---|
| 卡片尺寸 | w≈188–193, h≈214–245（上下列被面板邊緣切到時 h 會變小） |
| 長寬比 | 0.77–0.90 |
| 欄距 pitch | 273 px，均勻 |
| 邊框 hue（OpenCV H, 0–179） | 洋紅 145–160 / 深紫 130–145 / 藍 100–115 / 橘 8–22 |
| 飽和度門檻 | S > 120, V > 120 |

這些是單一解析度的觀測值，實作時**全部要換算成相對比例**，不可寫死像素。

### 7.3 模板來源

使用者提供的 5 張截圖，每張 12 格 × 5 張 = **60 格，剛好無重疊涵蓋全部 60 張卡**。
放在 `samples/`，切出來的模板存 `data/templates/{card_id}.png`。

灰階的 20 張不影響 — 比對本來就轉灰階做。

### 7.4 已知風險

- 模板只來自單一解析度。**上線前先做交叉驗證**（部分當模板、部分當測試）確認準確率，
  再決定要不要加銳利度/縮放容錯。
- 新獲得卡片可能有閃光特效蓋住卡面。
- 不同語言設定不影響（比對的是美術不是文字）。

### 7.5 相依

`opencv-python-headless` + `numpy` + `Pillow`。映像檔約多 40 MB，主機（7.1 GB RAM）可負擔。

---

## 8. API

```
POST   /api/players/verify      { tag, token }  → 驗證並綁定村莊
GET    /api/me                                   → user + 旗下所有村莊 + 目前選擇
POST   /api/me/active           { tag }          → 切換操作中的村莊
DELETE /api/players/{tag}                        → 解除綁定
GET    /api/cards                                → 卡表
GET    /api/collection/{tag}                     → 讀收藏
PUT    /api/collection/{tag}    { counts }       → 存收藏
GET    /api/matches?same_clan=1                  → 配對結果（會先同步部落）
POST   /api/recognize           multipart 圖片   → 階段二，回傳辨識結果供確認
```

### CoC API 注意事項

- Base `https://api.clashofclans.com/v1`，header `Authorization: Bearer <COC_API_KEY>`
- tag 要 URL-encode：`#` → `%23`，字母轉大寫。**未編碼的 `#` 會得到 404 而不是錯誤訊息**（實測），
  這種 404 很容易被誤判成「查無此玩家」，tag 正規化要在同一個地方做掉。
- **API key 綁來源 IP**（本 key 綁 `<伺服器公網 IP>`）。IP 一變全部 403 —
  錯誤訊息要明確講出這件事，不要讓人以為是別的問題。
- key 只放伺服器 `.env`，不得出現在前端或版控

#### 實測結果（2026-08-14，developer/silver tier）

| 狀況 | 回應 |
|---|---|
| 正常查詢 | 200 |
| 不存在的 tag | 404 `{"reason":"notFound"}` |
| `#` 未 URL-encode | 404（**不是 400**） |
| 缺 Authorization | 403 |
| 來源 IP 不符 | 403（與缺 header 同碼，需靠 body 的 `reason` 區分） |

`GET /players/{tag}` 確認有我們要的欄位：`tag` / `name` / `clan.tag` / `clan.name`。
玩家無部落時 `clan` 欄位直接不存在，讀取要用 `.get()`。

#### 效能與並發（實測 50 名成員）

| 方式 | 耗時 | 每個 |
|---|---|---|
| curl 每次重開連線 | — | ~820 ms |
| 連線池 + 循序 | 12.82 s | 256 ms |
| 連線池 + **並發 10** | **1.91 s** | 38 ms |
| 連線池 + 並發 30 | 1.42 s | 28 ms |

全部回 200，未觸發限流。

**結論 → 部落同步必須是非同步並發的**：
- 用單一 `httpx.AsyncClient`（連線池復用，光這點就快 3 倍）
- `asyncio.Semaphore(10)` 限流。並發 30 只多快 0.5 秒，不值得去壓官方 API
- 配合 `players.clan_synced_at` 快取（TTL 建議 10 分鐘），
  只重抓過期的，一般情況下配對根本不會打 API

若改成循序同步寫法，50 人的部落每次配對要等 13 秒 — 這是不可接受的，
所以 `services/coc.py` 從一開始就要寫成 async。

---

## 9. 前端

1. **登入** — tag + 權杖，附遊戲內取得步驟圖文
2. **我的收藏** — 60 格格狀，每格一個 **0–10 的下拉選單**（預設 0）。
   **不用點一下循環的做法** — 實測截圖裡已出現 `x4`（熔岩獵犬），張數上限不確定，
   循環點選在張數多時要點很多下。
   手機上要能快速填完 60 格是主要的 UX 目標：選單要夠大好按，
   且填完一格自動不跳頁（避免捲動位置跑掉）。
3. **配對** — 互利 / 我受益 / 我幫人 三區塊，顯示對方暱稱與所在部落，
   「只顯示同部落」開關（預設開）
4. **村莊切換** — 頂部選擇器
5. （階段二）**上傳截圖** — 上傳 → 辨識 → 人工確認 → 寫入

原生 HTML/CSS/JS，不引框架。

---

## 10. 技術棧與部署

- Python + FastAPI + SQLite，分層沿用 camera-viewer 的 `routers/` `services/` 慣例
- ruff + pytest
- Docker，接 infra 的 `edge` 網路
- **Port 3848**（3847 camera-viewer、8000 autocare、8555 go2rtc 已佔用）
- 子網域 `coc-cards.592355.xyz`，**橘雲必須 `import cloudflare_only`**（infra 紅線 1）
- **不掛 Cloudflare Access** — 認證由應用自己的權杖驗證負責，
  部落成員不需要被逐一加進允許名單
- Caddy upstream 用 `dynamic a`（infra 紅線 2）

部署：`git push` → `ssh server 'cd ~/coc-cards && ./update.sh'`，
Caddyfile 那段改在 infra repo。

commit 訊息用中文、一件事一個 commit、不加 AI 署名。

---

## 11. 階段

**階段一** — 權杖驗證登入、多帳號綁定、手動點選收藏、兩兩配對、部落顯示與過濾、部署上線

**階段二** — 截圖辨識（先驗證準確率再接上）

---

## 12. 待確認

- [ ] 60 張卡的名字草稿逐一核對（`data/cards.json` 的 `confirmed` 欄）
- [ ] 商人張數規則（一般卡 2 張 / 超級兵種卡 3 張？）要不要做進網頁
- [x] CoC API key 已取得並實測可用（綁 `<伺服器公網 IP>`；開發機與伺服器共用同一公網 IP，本機即可開發）
- [x] `verifytoken` 端對端實測通過（`#9QRUL2CVJ` / 炭烤椒鹽海豹 / TH18 / 《天堂》`#0CUY8QRL2` 32 人）
- [x] 5 張原始截圖已放進 `samples/`（`IMG_4926`~`IMG_4930.PNG`，依編號＝相簿順序）
