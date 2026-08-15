"""`POST /api/players/verify` 的頻率上限。

保護的不是密碼（這個站沒有密碼），是那把共用的 CoC API key 的配額。
每個測試釘的是這個前提推導出來的設計決定。
"""

from services import ratelimit


def test_驗證超過五次就被限流(client):
    for i in range(ratelimit.MAX_ATTEMPTS):
        res = client.post("/api/players/verify", json={"tag": "#AAA", "token": "badtok"})
        assert res.status_code == 401, f"第 {i + 1} 次就被擋了"

    res = client.post("/api/players/verify", json={"tag": "#AAA", "token": "badtok"})
    assert res.status_code == 429
    assert int(res.headers["retry-after"]) > 0


def test_成功的驗證也要計數(client):
    """只算失敗次數的話，一個持有合法村莊的人可以無限次「重新驗證」把整個
    部落共用的配額打爆 —— 而且每一次都是成功的請求。"""
    for i in range(ratelimit.MAX_ATTEMPTS):
        res = client.post("/api/players/verify", json={"tag": f"#V{i}", "token": "goodtok"})
        assert res.status_code == 200

    assert client.post("/api/players/verify", json={"tag": "#VX", "token": "goodtok"}).status_code == 429


def test_限流在打_coc_api_之前就發生(client, monkeypatch):
    """先付成本再限流等於沒有限流。"""
    from services import coc

    calls = []

    async def counting_verify(tag, token):
        calls.append(tag)
        return False

    monkeypatch.setattr(coc, "verify_token", counting_verify)

    for _ in range(ratelimit.MAX_ATTEMPTS + 3):
        client.post("/api/players/verify", json={"tag": "#AAA", "token": "badtok"})

    assert len(calls) == ratelimit.MAX_ATTEMPTS


def test_其他端點不受影響(client):
    """限流只掛在會打 CoC API 的端點上。整站限流會讓一個人拖垮所有人。"""
    assert client.post("/api/players/verify", json={"tag": "#AAA", "token": "goodtok"}).status_code == 200
    for _ in range(ratelimit.MAX_ATTEMPTS * 2):
        assert client.get("/api/me").status_code == 200


# ── 來源 IP ────────────────────────────────────────────────────────


class _FakeRequest:
    def __init__(self, peer, headers=None):
        self.client = type("C", (), {"host": peer})()
        self.headers = headers or {}


def test_只有內網對端送來的_x_real_ip_才採信():
    """外部直連的人自己送一個 X-Real-IP 進來不算數 —— 不然限流形同虛設。"""
    trusted = _FakeRequest("172.30.0.5", {"x-real-ip": "203.0.113.9"})
    assert ratelimit.client_ip(trusted) == "203.0.113.9"

    spoofed = _FakeRequest("8.8.8.8", {"x-real-ip": "203.0.113.9"})
    assert ratelimit.client_ip(spoofed) == "8.8.8.8"


def test_保留網段不算內網對端():
    """`ipaddress.is_private` 對 TEST-NET 這類保留段回 True，用它當信任判斷
    會把一批位址誤放進來。這個測試釘住的是「用明確清單」這個決定。"""
    spoofed = _FakeRequest("198.51.100.7", {"x-real-ip": "203.0.113.9"})
    assert ratelimit.client_ip(spoofed) == "198.51.100.7"


def test_不看_x_forwarded_for():
    """反向代理是**附加**而非覆寫 XFF，最左邊那一段是客戶端自己填的。

    改用 XFF 的話，每次請求換一個假 IP 就能無限次驗證。
    """
    req = _FakeRequest("172.30.0.5", {"x-forwarded-for": "1.2.3.4, 172.30.0.5"})
    assert ratelimit.client_ip(req) == "172.30.0.5"


def test_同一個來源共用計數而不同來源各自獨立():
    limiter = ratelimit.RateLimiter(max_attempts=2, window_seconds=60)
    assert limiter.hit("a")[0] is True
    assert limiter.hit("a")[0] is True
    assert limiter.hit("a")[0] is False
    assert limiter.hit("b")[0] is True
