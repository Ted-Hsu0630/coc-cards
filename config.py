"""設定。全部從環境變數讀，不在程式碼裡放預設的機密值。"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent

# 容器裡是靠 compose 的 env_file 注入，本機開發才吃這個檔。
# override=False：已存在的環境變數優先，避免 .env 蓋掉正式設定。
load_dotenv(BASE_DIR / ".env", override=False)

COC_API_KEY = os.environ.get("COC_API_KEY", "")
COC_API_BASE = "https://api.clashofclans.com/v1"

# 沒有 SECRET_KEY：session token 是 secrets.token_urlsafe(32) 直接存在資料庫裡的
# 不透明隨機值，不帶任何內容也不需要簽章。要改成簽章式 cookie 才需要加回來。
# data/ 在正式環境是 Docker named volume，只放**會變動的狀態**（資料庫）。
# 靜態資料一律放 assets/ —— named volume 只在第一次建立時從映像檔複製內容，
# 之後就完全遮蔽映像檔，放在 data/ 底下的靜態檔案更新永遠不會生效。
# 卡表原本就踩在這個坑上（線上那份是 8/14 首次建容器時複製的），
# 只是內容剛好還沒變過所以沒被發現。
DB_PATH = BASE_DIR / os.environ.get("DB_PATH", "data/coc-cards.db")
CARDS_PATH = BASE_DIR / "assets" / "cards.json"

SESSION_DAYS = 30
SESSION_COOKIE = "coc_cards_session"
# 正式站走 HTTPS 一律要 secure；本機 http://localhost 開發時才關掉，
# 否則瀏覽器會直接丟掉 cookie，登入看起來像是「成功但沒登入」。
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") != "0"

# 部落資訊快取多久才需要重抓。見 SPEC §8：配對前只重抓過期的，
# 一般情況根本不會打 API。
CLAN_CACHE_SECONDS = 600

# 同時打 CoC API 的上限。實測並發 10 就把 50 人從 12.8s 壓到 1.9s，
# 並發 30 只多快 0.5s —— 不值得去壓官方 API。
COC_CONCURRENCY = 10

# 背景重新整理部落資訊的間隔。設 0 可以整個關掉（測試就是這樣）。
# 取快取壽命的一半：使用者永遠不會等到 sync_clans，因為排程總是搶在過期之前
# 把資料換新。真正撐住正確性的仍然是 CLAN_CACHE_SECONDS —— 排程只是提前跑，
# 沒跑成功的話路由裡那道同步呼叫還是會補上（只是使用者要等一下）。
CLAN_REFRESH_SECONDS = int(os.environ.get("CLAN_REFRESH_SECONDS", CLAN_CACHE_SECONDS // 2))
COC_TIMEOUT = 20.0

# 每張卡的持有數上限（UI 下拉選單範圍 0..MAX_COUNT）
MAX_COUNT = 10
