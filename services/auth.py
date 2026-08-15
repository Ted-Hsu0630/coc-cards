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


def purge_expired_sessions(conn) -> int:
    """清掉過期的 session，回傳刪除筆數。

    `expires_at` 一律是 `db.now()` 產出的 UTC ISO8601（同格式、同 +00:00 結尾），
    所以字串比大小等同時間比大小，不需要逐列解析。
    """
    cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))
    return cur.rowcount


def create_session(conn, user_id: int, active_tag: str | None) -> str:
    # 搭登入的順風車清一次。session 只在登入時產生，所以這裡跑就夠密集了，
    # 不值得為它多養一個背景排程。
    purge_expired_sessions(conn)

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


def set_active_tag(conn, token: str, tag: str | None) -> None:
    conn.execute("UPDATE sessions SET active_tag = ? WHERE token = ?", (tag, token))


def resolve_active_tag(conn, sess: dict) -> str | None:
    """回傳這個 session 目前實際可用的村莊，必要時修正並寫回。

    `active_tag` 會失效：同一個帳號在另一台裝置解除綁定、或村莊被併到別的帳號時，
    舊 session 仍指著那個標籤。不修正的話使用者會看到一張**空白的收藏表**
    （以為資料不見了），重填之後按儲存還會被 403 擋掉。

    所以這裡不只是檢查，還會退回到第一個仍持有的村莊並寫回 session。
    """
    owned = [p["tag"] for p in players.players_of_user(conn, sess["user_id"])]
    if not owned:
        if sess.get("active_tag") is not None:
            set_active_tag(conn, sess["token"], None)
        return None

    tag = sess.get("active_tag")
    if tag not in owned:
        tag = owned[0]
        set_active_tag(conn, sess["token"], tag)
    return tag


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
        # CoC 對「不存在的標籤」也是回 200 + status=invalid，不會回 404
        # （實測），所以光看 verifytoken 分不出使用者是標籤打錯還是權杖打錯。
        # 不分清楚的話，標籤打錯的人會一直回遊戲重抓權杖卻永遠過不了。
        # 多打一次 GET /players 來區分 —— 只在失敗路徑上付這個成本。
        await coc.get_player(tag)  # 標籤不存在會拋 PlayerNotFound
        raise VerificationFailed("權杖不正確，請回遊戲重新顯示一次")

    info = await coc.get_player(tag)

    migrated = False
    if user_id is None:
        # 未登入狀態下驗證成功。如果這個村莊已經綁過，就登入它原本所屬的帳號 ——
        # 權杖驗證通過本身就是所有權的證明，用哪一個村莊登入都該進到同一個帳號。
        # 不這樣做的話，用已綁定的小號登入會撞到 TagAlreadyBound，而且訊息會說
        # 「已綁定在其他帳號」，但那其實是他自己的帳號，等於小號永遠登不進來。
        existing = players.get_player(conn, tag)
        user_id = existing["user_id"] if existing else _create_user(conn)
    else:
        # 已登入狀態下加綁。村莊若已屬於另一個「只有它一個」的帳號就直接搬過來，
        # 讓先各自單獨登入的人事後還能合併。
        migrated = players.adopt_player(conn, tag, user_id)

    players.upsert_player(conn, tag, user_id, info)

    return {"user_id": user_id, "tag": tag, "player": info, "migrated": migrated}
