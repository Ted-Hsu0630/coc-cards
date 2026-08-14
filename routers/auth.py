"""登入、村莊綁定與切換。"""

import sqlite3

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

import config
from routers.deps import get_conn, optional_session, require_session
from services import auth, coc, players

router = APIRouter(prefix="/api", tags=["auth"])


class VerifyIn(BaseModel):
    tag: str = Field(min_length=2, max_length=20)
    token: str = Field(min_length=4, max_length=64)


@router.post("/players/verify")
async def verify(
    body: VerifyIn,
    response: Response,
    conn: sqlite3.Connection = Depends(get_conn),
    sess: dict | None = Depends(optional_session),
):
    """驗證遊戲內權杖。已登入時視為加綁小號，未登入時建立新帳號。"""
    user_id = sess["user_id"] if sess else None
    try:
        result = await auth.verify_and_bind(conn, body.tag, body.token, user_id)
    except auth.VerificationFailed as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e
    except players.TagAlreadyBound as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except coc.PlayerNotFound as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "查無此村莊標籤") from e
    except coc.CocAuthError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    except coc.CocError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"CoC API 暫時無法使用：{e}") from e

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

    return {"player": result["player"], "active_tag": result["tag"]}


@router.get("/me")
def me(
    conn: sqlite3.Connection = Depends(get_conn),
    sess: dict | None = Depends(optional_session),
):
    if sess is None:
        return {"logged_in": False}
    mine = players.players_of_user(conn, sess["user_id"])
    active = sess.get("active_tag") or (mine[0]["tag"] if mine else None)
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
        raise HTTPException(status.HTTP_403_FORBIDDEN, "這個村莊不屬於你的帳號")
    auth.set_active_tag(conn, sess["token"], tag)
    return {"active_tag": tag}


@router.delete("/players/{tag}")
def unbind(
    tag: str,
    conn: sqlite3.Connection = Depends(get_conn),
    sess: dict = Depends(require_session),
):
    tag = players.normalize_tag(tag)
    if not players.unbind_player(conn, tag, sess["user_id"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到這個村莊")
    if sess.get("active_tag") == tag:
        rest = players.players_of_user(conn, sess["user_id"])
        auth.set_active_tag(conn, sess["token"], rest[0]["tag"] if rest else None)
    return {"ok": True}


@router.post("/logout")
def logout(
    response: Response,
    conn: sqlite3.Connection = Depends(get_conn),
    coc_cards_session: str | None = Cookie(default=None),
):
    auth.destroy_session(conn, coc_cards_session)
    response.delete_cookie(config.SESSION_COOKIE)
    return {"ok": True}
