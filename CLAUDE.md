# CLAUDE.md

部落衝突「卡牌衝突」換卡配對站。

**動手前先讀 [SPEC.md](SPEC.md)** —— 交換規則、卡表結構、CoC API 的實測行為都在那裡，
而且每個數字都是實測來的，不是猜的。[README.md](README.md) 講怎麼跑。

## 紅線

1. **`verifytoken` 驗證失敗也回 HTTP 200**，只有 body 的 `status` 欄位不同
   （`ok` vs `invalid`）。只檢查狀態碼或用 `raise_for_status()`，
   **任何人都能冒充任何玩家**。`tests/test_coc_verify.py` 就是為了守這條而存在。

2. **交換規則的兩個不對稱點不可簡化成對稱**（SPEC §2.2）：
   - 發起方換入的卡必須是自己**完全沒有**的（`count == 0`）
   - 接收方換入**沒有任何限制**（已擁有也照收）

   看起來像是可以合併的兩個條件，其實不行。`tests/test_matching.py` 釘住了每個角落。

3. **只有一張的卡不能送出**。門檻是 `count >= 2` 不是 `>= 1`。

4. **只能同系列互換**（聖水↔聖水…）。已在遊戲內確認，跨系列一律不成立。

5. **CoC API 的呼叫一律 async 且並發上限 10**。循序寫法在 50 人的部落要 12.8 秒
   （實測），並發 10 只要 1.9 秒。不要為了「簡單」改回同步。

6. **`db.connect()` 的 `check_same_thread=False` 不可拿掉**，
   但也**不可改成模組層級的共用連線**。理由寫在該行的註解裡。

7. **首頁要走 `no_cache_page()`，不可以改回 `FileResponse`**。
   它會在送出時把 `?v=<mtime>` 注入 CSS/JS/圖片的 URL —— 這是部署後強制更新的唯一手段。
   單靠 `Cache-Control: no-cache` 不夠：那只保證「用之前先問伺服器」，
   而使用者常常整天不重新整理，手機瀏覽器也可能整個略過重驗證。
   同理 **HTML 原始檔裡不可以手寫 `?v=`**，會跟注入疊加。
   做法與 All-in-One Downloader 的 `app/web/http_utils.py` 相同，
   `tests/test_asset_versioning.py` 守著。

8. **卡表的名字可以錯，結構不可以錯**。配對只吃 id 與 series。
   `core/cards.py` 在啟動時驗證張數（19/13/11/17 = 60），對不上就讓程式炸掉 ——
   配對算錯是靜默的，啟動失敗是吵的。

## 卡表

`data/cards.json`。名字有 `confirmed` 欄位，`false` 代表還沒跟遊戲畫面核對過，
前端會在名字後面顯示 `?`。**校正就直接改這個檔，網頁上刻意不留編輯入口**（使用者要求）。

## Git commit

- commit 訊息用**中文**、**一件事一個 commit**
- **不要**加 `Co-Authored-By` 之類的 AI 署名
