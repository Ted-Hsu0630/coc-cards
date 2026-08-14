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

SECRET_KEY = os.environ.get("SECRET_KEY", "")
DB_PATH = BASE_DIR / os.environ.get("DB_PATH", "data/coc-cards.db")
CARDS_PATH = BASE_DIR / "data" / "cards.json"

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
COC_TIMEOUT = 20.0

# 每張卡的持有數上限（UI 下拉選單範圍 0..MAX_COUNT）
MAX_COUNT = 10
