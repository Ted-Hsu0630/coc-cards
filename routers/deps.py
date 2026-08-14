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
    coc_cards_session: str | None = Cookie(default=None),
) -> dict | None:
    return auth.load_session(conn, coc_cards_session)


def require_session(sess: dict | None = Depends(optional_session)) -> dict:
    if sess is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "尚未登入")
    return sess


def require_active_tag(sess: dict = Depends(require_session)) -> str:
    tag = sess.get("active_tag")
    if not tag:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "尚未選擇村莊")
    return tag


COOKIE_NAME = config.SESSION_COOKIE
