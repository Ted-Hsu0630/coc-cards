"""共用的 FastAPI 相依。"""

import sqlite3
from collections.abc import Iterator

from fastapi import Cookie, Depends, HTTPException, status

import config
from core import db
from services import auth


def get_conn() -> Iterator[sqlite3.Connection]:
    with db.session() as conn:
        yield conn


def optional_session(
    conn: sqlite3.Connection = Depends(get_conn),
    # alias 而不是靠參數名稱：cookie 名稱只有 config.SESSION_COOKIE 一個來源，
    # 改設定時不會出現「參數名沒跟著改、驗證安靜失效」的狀況。
    token: str | None = Cookie(default=None, alias=config.SESSION_COOKIE),
) -> dict | None:
    return auth.load_session(conn, token)


def require_session(sess: dict | None = Depends(optional_session)) -> dict:
    if sess is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "尚未登入")
    return sess


def require_active_tag(
    conn: sqlite3.Connection = Depends(get_conn),
    sess: dict = Depends(require_session),
) -> str:
    """目前操作中的村莊，**保證是這個帳號現在持有的**。

    session 裡的 active_tag 可能已經失效（另一台裝置解除綁定、村莊被併走），
    resolve_active_tag 會自動退回到仍持有的村莊。有了這層保證，
    下游就不必再各自檢查擁有權。
    """
    tag = auth.resolve_active_tag(conn, sess)
    if not tag:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "尚未綁定任何村莊")
    return tag
