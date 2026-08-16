"""配對結果。"""

import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from core import cards
from routers.deps import get_conn, require_active_tag
from services import coc, matching, planning, players

# 多人計劃的成本大約是人數的平方（要看每一對的每個組合），而它是同步算在
# 請求裡的。20 個人已經遠超一個部落實際會一起換卡的規模，設上限只是避免
# 有人手動送一份很大的清單進來把單執行緒的伺服器卡住。
MAX_PLAN_PLAYERS = 20
MAX_PLAN_STEPS = 6

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


class PlanRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)
    favor: str | None = None
    max_steps: int = planning.MAX_STEPS_DEFAULT


@router.post("/matches/plan")
def plan_trades(
    req: PlanRequest,
    conn: sqlite3.Connection = Depends(get_conn),
    tag: str = Depends(require_active_tag),
):
    """一群人之間的換卡計劃，分成可同時執行的步驟。

    **不做部落同步。** 這支是使用者按下按鈕才算的，而計劃只吃收藏資料，
    部落資訊在這裡完全用不到 —— 為了它去等一趟 CoC API 是白等。
    """
    everyone = players.all_players(conn)

    # **不自動把自己加進去。** 幫部落其他人排一份計劃是合理的用法，硬塞會讓
    # 使用者在畫面上取消勾選卻發現自己還在結果裡。去重是必要的：同一個 tag
    # 送兩次的話他會變成可以跟自己交換。
    wanted = list(dict.fromkeys(req.tags))
    unknown = [t for t in wanted if t not in everyone]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "名單裡有查不到的村莊")
    if len(wanted) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "至少要選兩個人")
    if len(wanted) > MAX_PLAN_PLAYERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"一次最多 {MAX_PLAN_PLAYERS} 人"
        )
    if req.favor is not None and req.favor not in wanted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "優先對象不在名單裡")
    collections = players.all_collections(conn)
    steps = planning.plan(
        collections,
        wanted,
        max_steps=max(1, min(req.max_steps, MAX_PLAN_STEPS)),
        favor=req.favor,
    )
    summary = planning.summarize(steps)

    # 「換之前」要用**算這份計劃時的那份收藏**，不可以讓前端拿部落總覽那支的
    # 數字去湊。那支是進畫面時抓的，中間有人存了新庫存的話，畫面上就會出現
    # 「41/60 → 53/60」這種跟計劃對不起來的算式，而且完全看不出哪裡怪。
    all_ids = [c.id for c in cards.all_cards()]

    def owned(t: str) -> int:
        counts = collections.get(t, {})
        return sum(1 for c in all_ids if counts.get(c, 0) > 0)

    # 前端只拿得到 tag，其餘都要從這裡帶過去
    who = {}
    for t in wanted:
        before = owned(t)
        who[t] = {
            "name": everyone[t]["name"],
            "clan_name": everyone[t]["clan_name"],
            "collection_updated_at": everyone[t].get("collection_updated_at"),
            "collected": before,
            "after": before + summary["gained"].get(t, 0),
            "total": len(all_ids),
        }

    return {
        "tag": tag,
        "favor": req.favor,
        "steps": steps,
        "summary": summary,
        "players": who,
    }


@router.get("/clan/overview")
async def clan_overview(
    same_clan: bool = Query(default=True),
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
    all_ids = [c.id for c in cards.all_cards()]
    total = len(all_ids)
    me = everyone.get(tag, {})
    my_clan = me.get("clan_tag")

    rows = []
    for t, info in everyone.items():
        in_my_clan = bool(my_clan) and info["clan_tag"] == my_clan
        # 預設只看同部落：這個站不限部落，全部列出來就變成一份陌生人名單，
        # 也把每個註冊者的暱稱與進度攤給所有人看。自己的村莊一律保留。
        if same_clan and not in_my_clan and t != tag:
            continue
        counts = collections.get(t, {})
        rows.append(
            {
                "tag": t,
                "name": info["name"],
                "clan_tag": info["clan_tag"],
                "clan_name": info["clan_name"],
                "same_clan": in_my_clan,
                "collected": sum(1 for c in all_ids if counts.get(c, 0) > 0),
                "total": total,
                "has_data": bool(counts),
                "collection_updated_at": info.get("collection_updated_at"),
            }
        )
    rows.sort(key=lambda r: (not r["same_clan"], -r["collected"], r["name"]))
    return {"players": rows, "same_clan_only": same_clan}
