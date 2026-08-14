"""卡表與個人收藏。"""

import sqlite3

from fastapi import APIRouter, Body, Depends, HTTPException, status

import config
from core import cards
from routers.deps import get_conn, require_active_tag, require_session
from services import players

router = APIRouter(prefix="/api", tags=["collection"])


@router.get("/cards")
def list_cards():
    meta = cards.series_meta()
    return {
        "max_count": config.MAX_COUNT,
        "series": [
            {"key": k, "name_zh": v["name_zh"], "count": v["count"], "border": v["border"]}
            for k, v in meta.items()
        ],
        "cards": [c.as_dict() for c in cards.all_cards()],
    }


@router.get("/collection")
def read_collection(
    conn: sqlite3.Connection = Depends(get_conn),
    tag: str = Depends(require_active_tag),
):
    return {"tag": tag, "counts": players.get_collection(conn, tag)}


@router.put("/collection")
def write_collection(
    counts: dict[str, int] = Body(embed=True),
    conn: sqlite3.Connection = Depends(get_conn),
    sess: dict = Depends(require_session),
    tag: str = Depends(require_active_tag),
):
    owned = {p["tag"] for p in players.players_of_user(conn, sess["user_id"])}
    if tag not in owned:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "這個村莊不屬於你的帳號")
    try:
        saved = players.save_collection(conn, tag, counts)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"tag": tag, "counts": saved}
