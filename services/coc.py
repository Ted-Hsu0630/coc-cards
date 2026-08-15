"""Clash of Clans 官方 API 客戶端。

SPEC §8 的兩條實測結論直接決定了這個模組的寫法：

1. `verifytoken` 驗證失敗也回 HTTP 200 —— 必須看 body 的 status 欄位。
2. 循序打 API 在 50 人的部落要 12.8 秒，並發 10 只要 1.9 秒 ——
   所以全部寫成 async，共用一個 AsyncClient（連線池光是復用就快 3 倍）。
"""

import asyncio
import logging

import httpx

import config
from core import tags

log = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None
_sem: asyncio.Semaphore | None = None


class CocError(RuntimeError):
    """CoC API 呼叫失敗。"""


class CocAuthError(CocError):
    """403：金鑰無效，或**來源 IP 不在金鑰的白名單內**。"""


class PlayerNotFound(CocError):
    pass


class VerificationFailed(CocError):
    """權杖不正確。注意 CoC 對這種情況回的是 200，不是錯誤碼。"""


async def startup() -> None:
    global _client, _sem
    if not config.COC_API_KEY:
        log.warning("COC_API_KEY 未設定，所有 CoC API 呼叫都會失敗")
    _client = httpx.AsyncClient(
        base_url=config.COC_API_BASE,
        headers={"Authorization": f"Bearer {config.COC_API_KEY}"},
        timeout=config.COC_TIMEOUT,
        limits=httpx.Limits(max_connections=config.COC_CONCURRENCY * 2),
    )
    _sem = asyncio.Semaphore(config.COC_CONCURRENCY)


async def shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _request(method: str, path: str, **kw) -> httpx.Response:
    if _client is None or _sem is None:
        raise CocError("CoC 客戶端尚未初始化")
    async with _sem:
        try:
            resp = await _client.request(method, path, **kw)
        except httpx.RequestError as e:
            raise CocError(f"連線 CoC API 失敗：{e}") from e

    if resp.status_code == 403:
        # 缺 Authorization 與來源 IP 不符都是 403，只能靠 body 區分。
        # IP 不符是這個專案最可能的失敗原因，訊息要講清楚否則沒人查得出來。
        reason = _reason(resp)
        if "ip" in reason.lower():
            raise CocAuthError(
                "CoC API 拒絕存取：本機對外 IP 不在金鑰的白名單內。"
                "請到 developer.clashofclans.com 用目前的公網 IP 重建金鑰。"
            )
        raise CocAuthError(f"CoC API 拒絕存取（{reason or 'accessDenied'}）")
    if resp.status_code == 404:
        raise PlayerNotFound("查無此標籤")
    if resp.status_code == 429:
        raise CocError("CoC API 限流，請稍後再試")
    if resp.status_code >= 400:
        raise CocError(f"CoC API 回應 {resp.status_code}：{_reason(resp)}")
    return resp


def _reason(resp: httpx.Response) -> str:
    try:
        return resp.json().get("reason", "")
    except Exception:
        return ""


async def verify_token(tag: str, token: str) -> bool:
    """驗證玩家提供的遊戲內 API 權杖。

    🔴 SPEC §4.1 紅線：**驗證失敗時 CoC 一樣回 HTTP 200**，
    差別只在 body 的 `status` 欄位（`ok` vs `invalid`）。
    只檢查狀態碼或用 raise_for_status() 的話，任何人都能冒充任何玩家。
    """
    resp = await _request("POST", f"/players/{tags.encode(tag)}/verifytoken", json={"token": token})
    data = resp.json()
    return data.get("status") == "ok"


async def get_player(tag: str) -> dict:
    """回傳 {tag, name, clan_tag, clan_name}。玩家無部落時 clan 欄位整個不存在。"""
    resp = await _request("GET", f"/players/{tags.encode(tag)}")
    d = resp.json()
    clan = d.get("clan") or {}
    return {
        "tag": d.get("tag") or tags.normalize(tag),
        "name": d.get("name") or "",
        "clan_tag": clan.get("tag"),
        "clan_name": clan.get("name"),
    }


async def get_players(tag_list: list[str]) -> dict[str, dict | None]:
    """並發批次查詢。個別失敗不影響其他人，失敗者回 None。"""

    async def one(t: str):
        try:
            return t, await get_player(t)
        except PlayerNotFound:
            return t, None
        except CocError as e:
            log.warning("同步 %s 失敗：%s", t, e)
            return t, None

    results = await asyncio.gather(*(one(t) for t in tag_list))
    return dict(results)


async def get_clan(tag: str) -> dict:
    """回傳 {tag, name, members: [{tag, name}]}。

    `/clans/{tag}` 一次就給整份成員名單（含每個人的名字），所以部落同步不必
    逐人打 `/players/{tag}` —— 成本從「跟玩家數成正比」變成「跟部落數成正比」。
    實測正式機的兩個部落共 80 名成員，兩次呼叫就涵蓋完。
    """
    resp = await _request("GET", f"/clans/{tags.encode(tag)}")
    d = resp.json()
    return {
        "tag": d.get("tag") or tags.normalize(tag),
        "name": d.get("name") or "",
        "members": [
            {"tag": m["tag"], "name": m.get("name") or ""}
            for m in (d.get("memberList") or [])
            if m.get("tag")
        ],
    }


async def get_clans(tag_list: list[str]) -> dict[str, dict | None]:
    """並發批次查詢部落。個別失敗不影響其他人，失敗者回 None。

    失敗要回 None 而不是往上拋：某個部落被解散時，其他部落的成員照樣該更新，
    而查不到的那些人會退回逐人查詢。
    """

    async def one(t: str):
        try:
            return t, await get_clan(t)
        except PlayerNotFound:  # 404，部落解散或標籤打錯
            return t, None
        except CocError as e:
            log.warning("同步部落 %s 失敗：%s", t, e)
            return t, None

    results = await asyncio.gather(*(one(t) for t in tag_list))
    return dict(results)
