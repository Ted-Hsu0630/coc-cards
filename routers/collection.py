"""卡表與個人收藏。"""

import sqlite3

from fastapi import APIRouter, Body, Depends, HTTPException, status

import config
from core import cards
from routers.deps import get_conn, require_active_tag
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
    tag: str = Depends(require_active_tag),
):
    # 不再各自檢查擁有權：require_active_tag 保證回傳的一定是本帳號持有的村莊
    try:
        saved = players.save_collection(conn, tag, counts)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"tag": tag, "counts": saved}
