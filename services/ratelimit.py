"""來源限流。目前只有 `POST /api/players/verify` 在用。

## 保護的是什麼

不是密碼。這個站沒有密碼 —— 所有權的證明是遊戲內權杖，猜不出來也爆破不了。
要保護的是**那把共用的 CoC API key 的配額**：verify 每失敗一次會打兩次
CoC API（`verifytoken` 一次，再一次 `GET /players` 用來區分「標籤打錯」與
「權杖打錯」），有人拿腳本打它，燒掉的是整個部落共用的額度，所有人一起
登不進來。

因為要保護的是配額，所以**成功也要計數、成功也不清空**。只算失敗次數的話，
一個持有合法村莊的人可以無限次「重新驗證」把配額打爆，而每一次都是成功的。

## 記憶體，不是資料庫

限流狀態刻意不落地。容器重啟就重置是可以接受的 —— 攻擊者無法觸發重啟，
而重啟本來就不常發生。放進 SQLite 反而讓每次登入多一次寫入。
"""

import ipaddress
import threading
import time
from collections import defaultdict

WINDOW_SECONDS = 60
MAX_ATTEMPTS = 5
# 每累積 N 次檢查做一次全表清掃。單一來源的過期紀錄本來只在「同一個來源
# 再次嘗試」時才會被清掉，打一次就消失的來源（掃描器輪換 IP，尤其 IPv6）
# 會讓這張表只進不出、無上限成長。
SWEEP_EVERY_CHECKS = 64

# 反向代理可能出現的來源位址。**不要改用 `ipaddress.is_private`** —— 它比
# 名字聽起來寬得多，把 TEST-NET（198.51.100.0/24 之類）與其他保留段也算成
# 私有，等於把一批公網可路由性不明的位址一起放進信任範圍。寧可明確列出。
_TRUSTED_PEERS = tuple(
    ipaddress.ip_network(n)
    for n in ("127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def client_ip(request) -> str:
    """真實來源 IP。

    **只認 `X-Real-IP`，不要改成 `X-Forwarded-For`。** 反向代理是「附加」而非
    覆寫 XFF，所以 XFF 的最左邊那一段是**客戶端自己填的**，拿它當來源等於讓
    任何人每次請求換一個假 IP 就繞過限流。`X-Real-IP` 由代理整個覆寫，沒有
    這個問題。

    而且只有在對端本身是內網位址（＝反向代理）時才採信標頭。少了這層，任何
    能直接連到這個容器的人都能自己送一個 X-Real-IP 進來。
    """
    peer = request.client.host if request.client else ""
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return peer or "unknown"
    if any(addr in net for net in _TRUSTED_PEERS):
        real = (request.headers.get("x-real-ip") or "").strip()
        if real:
            return real
    return peer


class RateLimiter:
    def __init__(self, max_attempts: int = MAX_ATTEMPTS, window_seconds: int = WINDOW_SECONDS):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._checks_since_sweep = 0

    def _sweep(self, window_start: float) -> None:
        for key in list(self._hits):
            alive = [t for t in self._hits[key] if t > window_start]
            if alive:
                self._hits[key] = alive
            else:
                self._hits.pop(key, None)

    def hit(self, key: str) -> tuple[bool, int]:
        """記一次嘗試，回傳 (可不可以繼續, 還要等幾秒)。

        檢查與計數合成一個動作：分成兩支的話，呼叫端很容易在某條提早 return
        的路徑上忘記計數，而那正是攻擊者會走的那條。
        """
        now = time.time()
        window_start = now - self.window_seconds
        with self._lock:
            self._checks_since_sweep += 1
            if self._checks_since_sweep >= SWEEP_EVERY_CHECKS:
                self._checks_since_sweep = 0
                self._sweep(window_start)

            hits = [t for t in self._hits[key] if t > window_start]
            if len(hits) >= self.max_attempts:
                self._hits[key] = hits
                return False, int(min(hits) + self.window_seconds - now) + 1
            hits.append(now)
            self._hits[key] = hits
            return True, 0

    def reset(self) -> None:
        """測試用。正式流程沒有任何地方該呼叫它。"""
        with self._lock:
            self._hits.clear()
            self._checks_since_sweep = 0
