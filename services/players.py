"""村莊、收藏、部落同步。"""

import logging
from datetime import UTC, datetime, timedelta

import config
from core import cards, db, tags
from services import coc

log = logging.getLogger(__name__)


class TagAlreadyBound(RuntimeError):
    """這個 tag 已經綁在別人的帳號底下。"""


def _row_to_player(r) -> dict:
    return {
        "tag": r["tag"],
        "name": r["name"],
        "clan_tag": r["clan_tag"],
        "clan_name": r["clan_name"],
    }


def get_player(conn, tag: str) -> dict | None:
    r = conn.execute("SELECT * FROM players WHERE tag = ?", (tag,)).fetchone()
    return dict(r) if r else None


def players_of_user(conn, user_id: int) -> list[dict]:
    # sort_order 是使用者自訂的順序；同值時用 verified_at 當穩定的次要鍵
    rows = conn.execute(
        "SELECT * FROM players WHERE user_id = ? ORDER BY sort_order, verified_at", (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def _next_sort_order(conn, user_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM players WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(row["n"])


def set_order(conn, user_id: int, ordered_tags: list[str]) -> None:
    """依給定順序重排這個帳號的村莊。

    呼叫端必須先確認 ordered_tags 剛好等於這個帳號持有的村莊集合，
    否則會把別人的村莊或不存在的標籤寫進來。
    """
    for i, tag in enumerate(ordered_tags):
        conn.execute(
            "UPDATE players SET sort_order = ? WHERE tag = ? AND user_id = ?", (i, tag, user_id)
        )


def upsert_player(conn, tag: str, user_id: int, info: dict) -> None:
    """綁定或更新村莊。已綁在別的 user 底下時拒絕，不搶帳號。"""
    existing = conn.execute("SELECT user_id FROM players WHERE tag = ?", (tag,)).fetchone()
    if existing and existing["user_id"] != user_id:
        # 走到這裡代表 adopt_player 沒有處理掉，是防呆用的最後一道
        raise TagAlreadyBound(f"{tag} 已經綁在另一個帳號底下")

    ts = db.now()
    conn.execute(
        """
        INSERT INTO players (tag, user_id, name, clan_tag, clan_name,
                             clan_synced_at, verified_at, updated_at, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tag) DO UPDATE SET
            name = excluded.name,
            clan_tag = excluded.clan_tag,
            clan_name = excluded.clan_name,
            clan_synced_at = excluded.clan_synced_at,
            verified_at = excluded.verified_at,
            updated_at = excluded.updated_at
        """,
        # 新綁的村莊排在最後面。ON CONFLICT 刻意不更新 sort_order ——
        # 重新驗證既有村莊時不該把使用者調好的順序打亂。
        (
            tag,
            user_id,
            info["name"],
            info["clan_tag"],
            info["clan_name"],
            ts,
            ts,
            ts,
            _next_sort_order(conn, user_id),
        ),
    )


def adopt_player(conn, tag: str, target_user_id: int) -> bool:
    """把已經綁在別的帳號底下的村莊搬到 target_user_id，回傳是否真的搬了。

    使用情境（實際發生過）：成員先各自單獨登入兩隻帳號，各自建立了獨立帳號，
    之後才發現有加綁小號的功能 —— 那時兩邊已經合不起來，只會一直看到「已被綁定」。

    呼叫這裡之前權杖已經驗證過，所以擁有權沒有疑問。
    只有在來源帳號**只剩這一個村莊**時才搬 —— 那樣搬完來源帳號就是空的，
    可以直接刪掉，不會影響到任何其他村莊。收藏是以 tag 為鍵，搬家不會掉資料。
    """
    row = conn.execute("SELECT user_id FROM players WHERE tag = ?", (tag,)).fetchone()
    if row is None or row["user_id"] == target_user_id:
        return False

    source_id = row["user_id"]
    others = conn.execute(
        "SELECT name FROM players WHERE user_id = ? AND tag != ?", (source_id, tag)
    ).fetchall()
    if others:
        names = "、".join(r["name"] for r in others)
        raise TagAlreadyBound(
            f"{tag} 的帳號底下還有其他村莊（{names}）。"
            "請先登出，用那個帳號登入解除綁定，再回來加綁。"
        )

    # 先搬村莊再刪來源帳號 —— 順序反過來的話 players 會被外鍵 CASCADE 一起刪掉。
    # 刪掉來源帳號會連帶清掉它的 session，那是對的：這個村莊已經換人管了。
    conn.execute("UPDATE players SET user_id = ? WHERE tag = ?", (target_user_id, tag))
    conn.execute("DELETE FROM users WHERE id = ?", (source_id,))
    return True


def unbind_player(conn, tag: str, user_id: int) -> bool:
    cur = conn.execute("DELETE FROM players WHERE tag = ? AND user_id = ?", (tag, user_id))
    return cur.rowcount > 0


# --- 收藏 -------------------------------------------------------------------


def get_collection(conn, tag: str) -> dict[str, int]:
    rows = conn.execute("SELECT card_id, count FROM collections WHERE tag = ?", (tag,)).fetchall()
    return {r["card_id"]: r["count"] for r in rows}


def all_collections(conn) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in conn.execute("SELECT tag, card_id, count FROM collections"):
        out.setdefault(r["tag"], {})[r["card_id"]] = r["count"]
    return out


def save_collection(conn, tag: str, counts: dict[str, int]) -> dict[str, int]:
    """覆寫整份收藏。只存 count > 0 的列，查不到即視為 0（SPEC §5）。"""
    valid = cards.valid_ids()
    clean: dict[str, int] = {}
    for card_id, raw in counts.items():
        if card_id not in valid:
            raise ValueError("卡片資料有誤，請重新整理")
        n = int(raw)
        if n < 0 or n > config.MAX_COUNT:
            raise ValueError(f"張數只能填 0 到 {config.MAX_COUNT}")
        if n > 0:
            clean[card_id] = n

    conn.execute("DELETE FROM collections WHERE tag = ?", (tag,))
    conn.executemany(
        "INSERT INTO collections (tag, card_id, count) VALUES (?, ?, ?)",
        [(tag, c, n) for c, n in clean.items()],
    )
    return clean


# --- 部落同步 ----------------------------------------------------------------


def _is_stale(synced_at: str | None) -> bool:
    if not synced_at:
        return True
    try:
        ts = datetime.fromisoformat(synced_at)
        return datetime.now(UTC) - ts > timedelta(seconds=config.CLAN_CACHE_SECONDS)
    except (ValueError, TypeError):
        # 讀不懂的時間戳（例如早期寫入的無時區值，相減會拋 TypeError）
        # 一律當成過期重抓，不要讓整個配對頁 500
        return True


async def sync_clans(conn) -> int:
    """把過期的部落資訊重抓一次。回傳實際更新的筆數。

    SPEC §8：循序寫法在 50 人的部落要 12.8 秒，所以這裡走 coc.get_players 的
    並發批次。快取沒過期就完全不打 API —— 一般情況下配對是零外部呼叫。
    """
    rows = conn.execute("SELECT tag, clan_synced_at FROM players").fetchall()
    stale = [r["tag"] for r in rows if _is_stale(r["clan_synced_at"])]
    if not stale:
        return 0

    results = await coc.get_players(stale)
    ts = db.now()
    updated = 0
    for tag, info in results.items():
        if info is None:
            # 查不到就只推進時間戳，避免每次配對都重試同一個壞掉的 tag
            conn.execute("UPDATE players SET clan_synced_at = ? WHERE tag = ?", (ts, tag))
            continue
        conn.execute(
            """UPDATE players
               SET name = ?, clan_tag = ?, clan_name = ?, clan_synced_at = ?, updated_at = ?
               WHERE tag = ?""",
            (info["name"], info["clan_tag"], info["clan_name"], ts, ts, tag),
        )
        updated += 1
    log.info("部落同步：%d 個過期，%d 個更新成功", len(stale), updated)
    return updated


def all_players(conn) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM players").fetchall()
    return {r["tag"]: _row_to_player(r) for r in rows}


def normalize_tag(tag: str) -> str:
    return tags.normalize(tag)
