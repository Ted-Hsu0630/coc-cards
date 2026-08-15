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

9. **靜態資料一律放 `assets/`，不可以放 `data/`**。
   `data/` 在正式環境是 Docker named volume，只放會變動的狀態（資料庫）。
   named volume **只在第一次建立時**從映像檔複製內容，之後就完全遮蔽映像檔 ——
   放在 `data/` 底下的檔案更新永遠不會生效，而且完全沒有錯誤訊息。
   卡表原本就踩在這個坑上（線上那份是首次建容器時複製的），只是內容還沒變過。

10. **辨識結果不可以直接寫進資料庫**。`routers/importer.py` 只回傳提案，
    寫入一律走使用者按過確認的 `PUT /api/collection`。
    讀不出來的格子要標成「認不出」，**不可以猜 0 或 1** ——
    `tests/test_recognition.py` 守著這條，連「該拒答卻猜對」都算失敗。

11. **判「有沒有徽章」只能用字形切割，不可以用「底部邊框被切斷」**。
    後者在 iPhone 無損截圖上看起來完美（空隙 0.56），但超級部隊的橘邊框
    與徽章金色是同一個色相區間，前提根本不成立；JPEG 壓縮又會讓邊框色
    溢出卡片下緣。詳見 `tools/FINDINGS.md`。

12. **`services/__init__.py` 裡設像素上限的那幾行不可以搬走，也不可以清空那個檔案。**
    `OPENCV_IO_MAX_IMAGE_PIXELS` 只在 `import cv2` 之前設才有效，而
    `recognize.py` 與 `progress.py` 都在模組層級 import cv2。父套件的
    `__init__.py` 是唯一在 web app、`tools/`、pytest 三條路徑都保證跑在前面的
    位置。搬走不會有錯誤訊息，只是安靜地退回 OpenCV 的預設值（2³⁰ 像素，
    單張就能吃掉 3.2GB）。
    上限 24 Mpx 與 `MAX_IMAGES = 8` 是**一起**決定的：`analyze()` 會同時持有
    整批解碼後的點陣圖，8 × 72MB = 576MB 是這台 7GB 機器（還要跟 NVR 共用）
    的預算。改任一邊都要重算，`tests/test_limits.py` 守著那道乘法。

13. **限流取來源 IP 只認 `X-Real-IP`，不可以改用 `X-Forwarded-For`**。
    反向代理是「附加」而非覆寫 XFF，最左邊那一段是客戶端自己填的，
    拿它當 key 等於每次請求換一個假 IP 就能繞過限流。

## 卡表

`assets/cards.json`。名字有 `confirmed` 欄位，`false` 代表還沒跟遊戲畫面核對過，
前端會在名字後面顯示 `?`。**校正就直接改這個檔，網頁上刻意不留編輯入口**（使用者要求）。

## Git commit

- commit 訊息用**中文**、**一件事一個 commit**
- **不要**加 `Co-Authored-By` 之類的 AI 署名
