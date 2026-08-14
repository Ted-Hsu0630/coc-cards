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
    rows = conn.execute(
        "SELECT * FROM players WHERE user_id = ? ORDER BY verified_at", (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_player(conn, tag: str, user_id: int, info: dict) -> None:
    """綁定或更新村莊。已綁在別的 user 底下時拒絕，不搶帳號。"""
    existing = conn.execute("SELECT user_id FROM players WHERE tag = ?", (tag,)).fetchone()
    if existing and existing["user_id"] != user_id:
        raise TagAlreadyBound(f"{tag} 已綁定在其他帳號")

    ts = db.now()
    conn.execute(
        """
        INSERT INTO players (tag, user_id, name, clan_tag, clan_name,
                             clan_synced_at, verified_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tag) DO UPDATE SET
            name = excluded.name,
            clan_tag = excluded.clan_tag,
            clan_name = excluded.clan_name,
            clan_synced_at = excluded.clan_synced_at,
            verified_at = excluded.verified_at,
            updated_at = excluded.updated_at
        """,
        (tag, user_id, info["name"], info["clan_tag"], info["clan_name"], ts, ts, ts),
    )


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
            raise ValueError(f"未知的卡片 id：{card_id}")
        n = int(raw)
        if n < 0 or n > config.MAX_COUNT:
            raise ValueError(f"{card_id} 的張數 {n} 超出 0..{config.MAX_COUNT}")
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
    except ValueError:
        return True
    return datetime.now(UTC) - ts > timedelta(seconds=config.CLAN_CACHE_SECONDS)


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
