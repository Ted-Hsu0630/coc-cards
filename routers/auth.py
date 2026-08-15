"""登入、村莊綁定與切換。"""

import logging
import sqlite3

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

import config
from routers.deps import get_conn, optional_session, require_session
from services import auth, coc, players, ratelimit

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])

# CoC API 的失敗有五種（金鑰過期、IP 不在白名單、限流、5xx、連不上），但對使用者
# 都是同一件事：現在驗證不了，等一下再試。狀態碼與 reason 是拿來除錯的，寫進
# 日誌就好 —— 尤其「IP 不在白名單」是站方要處理的，部落成員看了也做不了什麼。
UPSTREAM_DOWN = "遊戲伺服器連線失敗，請稍後再試"

# 只有 verify 需要 —— 其餘端點不會打 CoC API，也就沒有配額可燒。
verify_limiter = ratelimit.RateLimiter()


class VerifyIn(BaseModel):
    tag: str = Field(min_length=2, max_length=20)
    token: str = Field(min_length=4, max_length=64)


@router.post("/players/verify")
async def verify(
    body: VerifyIn,
    request: Request,
    response: Response,
    conn: sqlite3.Connection = Depends(get_conn),
    sess: dict | None = Depends(optional_session),
):
    """驗證遊戲內權杖。已登入時視為加綁小號，未登入時建立新帳號。"""
    # 計數要在打 CoC API **之前**：限流保護的就是那把 key 的配額，
    # 放到後面等於每次被限流之前都已經先把成本付掉了（見 ratelimit.py）。
    allowed, retry_after = verify_limiter.hit(ratelimit.client_ip(request))
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"驗證太頻繁，請等 {retry_after} 秒再試",
            headers={"Retry-After": str(retry_after)},
        )

    user_id = sess["user_id"] if sess else None
    try:
        result = await auth.verify_and_bind(conn, body.tag, body.token, user_id)
    except auth.VerificationFailed as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    except players.TagAlreadyBound as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except coc.PlayerNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "查無此標籤") from e
    except coc.CocAuthError as e:
        # 這一條幾乎都是站方的設定問題（金鑰過期或對外 IP 變了），要看得見。
        log.error("CoC API 認證失敗：%s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, UPSTREAM_DOWN) from e
    except coc.CocError as e:
        log.warning("CoC API 失敗：%s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, UPSTREAM_DOWN) from e

    if sess is None:
        token = auth.create_session(conn, result["user_id"], result["tag"])
        response.set_cookie(
            config.SESSION_COOKIE,
            token,
            max_age=config.SESSION_DAYS * 86400,
            httponly=True,
            samesite="lax",
            secure=config.COOKIE_SECURE,
        )
    else:
        auth.set_active_tag(conn, sess["token"], result["tag"])

    return {
        "player": result["player"],
        "active_tag": result["tag"],
        "migrated": result["migrated"],
    }


@router.get("/me")
def me(
    conn: sqlite3.Connection = Depends(get_conn),
    sess: dict | None = Depends(optional_session),
):
    if sess is None:
        return {"logged_in": False}
    mine = players.players_of_user(conn, sess["user_id"])
    # 跟 require_active_tag 走同一條解析邏輯，否則會出現
    # 「/api/me 說目前是 X、但 /api/collection 用的是 Y」這種前後不一致
    active = auth.resolve_active_tag(conn, sess)
    return {
        "logged_in": True,
        "active_tag": active,
        "players": [
            {
                "tag": p["tag"],
                "name": p["name"],
                "clan_tag": p["clan_tag"],
                "clan_name": p["clan_name"],
            }
            for p in mine
        ],
    }


@router.post("/me/active")
def set_active(
    tag: str = Body(embed=True),
    conn: sqlite3.Connection = Depends(get_conn),
    sess: dict = Depends(require_session),
):
    tag = players.normalize_tag(tag)
    owned = {p["tag"] for p in players.players_of_user(conn, sess["user_id"])}
    if tag not in owned:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "此村莊不屬於你")
    auth.set_active_tag(conn, sess["token"], tag)
    return {"active_tag": tag}


@router.post("/me/order")
def set_order(
    tags: list[str] = Body(embed=True),
    conn: sqlite3.Connection = Depends(get_conn),
    sess: dict = Depends(require_session),
):
    """重排村莊順序，上方的下拉選單跟著這個順序。"""
    owned = {p["tag"] for p in players.players_of_user(conn, sess["user_id"])}
    try:
        wanted = [players.normalize_tag(t) for t in tags]
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    # 必須「剛好」是自己持有的那一組：少一個會留下沒排到的村莊，
    # 多一個或重複則會把別人的村莊寫進來
    if len(wanted) != len(owned) or set(wanted) != owned:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "排序失敗，請重新整理")

    players.set_order(conn, sess["user_id"], wanted)
    return {"players": [p["tag"] for p in players.players_of_user(conn, sess["user_id"])]}


@router.delete("/players/{tag}")
def unbind(
    tag: str,
    conn: sqlite3.Connection = Depends(get_conn),
    sess: dict = Depends(require_session),
):
    tag = players.normalize_tag(tag)
    if not players.unbind_player(conn, tag, sess["user_id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "查無此村莊")
    if sess.get("active_tag") == tag:
        rest = players.players_of_user(conn, sess["user_id"])
        auth.set_active_tag(conn, sess["token"], rest[0]["tag"] if rest else None)
    return {"ok": True}


@router.post("/logout")
def logout(
    response: Response,
    conn: sqlite3.Connection = Depends(get_conn),
    token: str | None = Cookie(default=None, alias=config.SESSION_COOKIE),
):
    auth.destroy_session(conn, token)
    response.delete_cookie(config.SESSION_COOKIE)
    return {"ok": True}
