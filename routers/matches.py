"""配對結果。"""

import logging
import sqlite3

from fastapi import APIRouter, Depends, Query

from core import cards
from routers.deps import get_conn, require_active_tag
from services import coc, matching, players

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["matches"])


@router.get("/matches")
async def get_matches(
    same_clan: bool = Query(default=True),
    conn: sqlite3.Connection = Depends(get_conn),
    tag: str = Depends(require_active_tag),
):
    # 配對前先把過期的部落資訊補上。快取沒過期就不打 API（SPEC §8）。
    clan_sync_ok = True
    try:
        await players.sync_clans(conn)
    except coc.CocError as e:
        # 同步失敗不該讓整頁掛掉 —— 用快取的部落資訊繼續，並告訴前端資料可能過時
        log.warning("部落同步失敗，改用快取：%s", e)
        clan_sync_ok = False

    everyone = players.all_players(conn)
    collections = players.all_collections(conn)
    result = matching.find_matches(tag, collections, everyone, same_clan_only=same_clan)

    mine = collections.get(tag, {})
    missing = [c.id for c in cards.all_cards() if mine.get(c.id, 0) == 0]
    spares = [c.id for c in cards.all_cards() if mine.get(c.id, 0) >= 2]

    return {
        "tag": tag,
        "same_clan_only": same_clan,
        "clan_sync_ok": clan_sync_ok,
        "total_players": len(everyone),
        "collected": len(cards.all_cards()) - len(missing),
        "missing": missing,
        "spares": spares,
        "matches": result,
    }


@router.get("/clan/overview")
async def clan_overview(
    conn: sqlite3.Connection = Depends(get_conn),
    tag: str = Depends(require_active_tag),
):
    """誰建好表了、誰還沒。"""
    try:
        await players.sync_clans(conn)
    except coc.CocError as e:
        log.warning("部落同步失敗：%s", e)

    everyone = players.all_players(conn)
    collections = players.all_collections(conn)
    total = len(cards.all_cards())
    me = everyone.get(tag, {})

    rows = []
    for t, info in everyone.items():
        counts = collections.get(t, {})
        rows.append(
            {
                "tag": t,
                "name": info["name"],
                "clan_tag": info["clan_tag"],
                "clan_name": info["clan_name"],
                "same_clan": bool(me.get("clan_tag")) and info["clan_tag"] == me.get("clan_tag"),
                "collected": sum(1 for c in cards.all_cards() if counts.get(c.id, 0) > 0),
                "total": total,
                "has_data": bool(counts),
            }
        )
    rows.sort(key=lambda r: (not r["same_clan"], -r["collected"], r["name"]))
    return {"players": rows}
