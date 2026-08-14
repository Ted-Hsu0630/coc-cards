"""登入、session、村莊綁定。

session 綁 user_id 不綁 tag（SPEC §4.2）—— 一個人可以有多個村莊，
切換村莊不該需要重新登入。
"""

import secrets
from datetime import UTC, datetime, timedelta

import config
from core import db
from services import coc, players


class VerificationFailed(RuntimeError):
    """權杖不正確。"""


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def create_session(conn, user_id: int, active_tag: str | None) -> str:
    token = _new_token()
    expires = (datetime.now(UTC) + timedelta(days=config.SESSION_DAYS)).isoformat()
    conn.execute(
        "INSERT INTO sessions (token, user_id, active_tag, expires_at, created_at) VALUES (?,?,?,?,?)",
        (token, user_id, active_tag, expires, db.now()),
    )
    return token


def load_session(conn, token: str | None) -> dict | None:
    if not token:
        return None
    r = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    if r is None:
        return None
    try:
        if datetime.fromisoformat(r["expires_at"]) < datetime.now(UTC):
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None
    except ValueError:
        return None
    return dict(r)


def destroy_session(conn, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def set_active_tag(conn, token: str, tag: str) -> None:
    conn.execute("UPDATE sessions SET active_tag = ? WHERE token = ?", (tag, token))


def _create_user(conn) -> int:
    cur = conn.execute("INSERT INTO users (created_at) VALUES (?)", (db.now(),))
    return int(cur.lastrowid)


async def verify_and_bind(conn, raw_tag: str, token: str, user_id: int | None) -> dict:
    """驗證遊戲內權杖並把村莊綁到帳號上。

    🔴 SPEC §4.1：`coc.verify_token` 內部檢查的是 response body 的 status 欄位，
    因為 CoC 在權杖錯誤時**一樣回 HTTP 200**。這裡不可以改成只看有沒有拋例外。
    """
    tag = players.normalize_tag(raw_tag)

    if not await coc.verify_token(tag, token.strip()):
        raise VerificationFailed("權杖不正確，或與這個村莊標籤不符")

    info = await coc.get_player(tag)

    if user_id is None:
        user_id = _create_user(conn)
    players.upsert_player(conn, tag, user_id, info)

    return {"user_id": user_id, "tag": tag, "player": info}
