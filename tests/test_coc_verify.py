"""🔴 SPEC §4.1 紅線的回歸測試。

CoC 的 verifytoken 在權杖錯誤時**一樣回 HTTP 200**，只有 body 的 status 欄位不同。
只看狀態碼（或 raise_for_status）就會讓任何人冒充任何玩家。

這個檔案存在的唯一理由是：日後有人把 verify_token 重構成「沒拋例外就算過」時，
測試要立刻紅給他看。
"""

import httpx
import pytest

from services import coc


class _FakeClient:
    """只回傳預先安排好的回應，不出網路。"""

    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.calls: list[tuple[str, str]] = []

    async def request(self, method, path, **kw):
        self.calls.append((method, path))
        return httpx.Response(
            self.status_code,
            json=self.payload,
            request=httpx.Request(method, "https://example.invalid" + path),
        )


@pytest.fixture
def patched(monkeypatch):
    import asyncio

    def _install(payload, status_code=200):
        client = _FakeClient(payload, status_code)
        monkeypatch.setattr(coc, "_client", client)
        monkeypatch.setattr(coc, "_sem", asyncio.Semaphore(1))
        return client

    return _install


@pytest.mark.asyncio
async def test_正確權杖回傳_True(patched):
    patched({"tag": "#ABC", "token": "good", "status": "ok"})
    assert await coc.verify_token("#ABC", "good") is True


@pytest.mark.asyncio
async def test_錯誤權杖即使回200也必須拒絕(patched):
    # 這正是實測到的回應：HTTP 200 + status=invalid
    patched({"tag": "#ABC", "token": "00000000", "status": "invalid"})
    assert await coc.verify_token("#ABC", "00000000") is False


@pytest.mark.asyncio
async def test_status欄位缺席時必須拒絕(patched):
    # 官方回應格式改變、或回了非預期的 body，一律當作驗證失敗，不可放行
    patched({"tag": "#ABC"})
    assert await coc.verify_token("#ABC", "whatever") is False


@pytest.mark.asyncio
async def test_status為其他值時必須拒絕(patched):
    patched({"tag": "#ABC", "status": "OK"})  # 大小寫不同也不算通過
    assert await coc.verify_token("#ABC", "whatever") is False


@pytest.mark.asyncio
async def test_tag中的井號要被編碼(patched):
    client = patched({"status": "ok"})
    await coc.verify_token("#9QRUL2CVJ", "tok")
    # 未編碼的 # 打 CoC API 會得到 404 而不是錯誤訊息（SPEC §8）
    assert client.calls == [("POST", "/players/%239QRUL2CVJ/verifytoken")]


@pytest.mark.asyncio
async def test_IP不符的403要給出可辨識的訊息(patched):
    patched({"reason": "accessDenied.invalidIp"}, status_code=403)
    with pytest.raises(coc.CocAuthError, match="白名單"):
        await coc.verify_token("#ABC", "tok")
