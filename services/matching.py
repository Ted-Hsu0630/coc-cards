"""換卡配對。實作 SPEC §6。

刻意寫成不碰資料庫的純函式 —— 配對正確性是這個站的全部價值，
必須能用單元測試把規則的每個角落釘死。

規則回顧（SPEC §2.2），一筆交換必有方向：

    發起方 I 送出 X   要求 I.count[X] >= 2
    發起方 I 收到 Y   要求 I.count[Y] == 0     ← 只能指定自己完全沒有的
    接收方 R 送出 Y   要求 R.count[Y] >= 2
    接收方 R 收到 X   無限制                    ← 已擁有也照收
    且 series(X) == series(Y)
"""

from dataclasses import dataclass, field

from core import cards

MUTUAL = "mutual"      # 互利互換：雙方各補一個空缺
INCOMING = "incoming"  # 我受益：我補一張，對方只是收下重複卡
OUTGOING = "outgoing"  # 我幫人：對方補一張，我收下一張自己可能已有的

# 誰必須先開口。只有發起方能指定「換入自己沒有的卡」，
# 所以受益的一方就是必須發起的一方。
INITIATOR = {MUTUAL: "either", INCOMING: "me", OUTGOING: "them"}

_KIND_RANK = {MUTUAL: 0, INCOMING: 1, OUTGOING: 2}


@dataclass
class SeriesMatch:
    series: str
    kind: str
    i_give: list[str] = field(default_factory=list)  # 我送出的候選卡
    i_get: list[str] = field(default_factory=list)   # 我收到的候選卡

    def as_dict(self) -> dict:
        return {
            "series": self.series,
            "kind": self.kind,
            "i_give": self.i_give,
            "i_get": self.i_get,
        }


@dataclass
class Match:
    tag: str
    name: str
    clan_tag: str | None
    clan_name: str | None
    same_clan: bool
    kind: str
    series: list[SeriesMatch]
    gain: int   # 這個對象總共能幫我補幾張
    help: int   # 我總共能幫對方補幾張

    def as_dict(self) -> dict:
        return {
            "tag": self.tag,
            "name": self.name,
            "clan_tag": self.clan_tag,
            "clan_name": self.clan_name,
            "same_clan": self.same_clan,
            "kind": self.kind,
            "initiator": INITIATOR[self.kind],
            "gain": self.gain,
            "help": self.help,
            "series": [s.as_dict() for s in self.series],
        }


def _spare(counts: dict[str, int], card_id: str) -> bool:
    """有多的可以送出。最後一張不能送，所以門檻是 2 不是 1。"""
    return counts.get(card_id, 0) >= 2


def _missing(counts: dict[str, int], card_id: str) -> bool:
    return counts.get(card_id, 0) == 0


def _series_match(
    series: str, ids: list[str], mine: dict[str, int], theirs: dict[str, int]
) -> SeriesMatch | None:
    # 我有多的、而對方完全沒有 —— 送給對方能填到對方的空缺
    fills_them = [c for c in ids if _spare(mine, c) and _missing(theirs, c)]
    # 對方有多的、而我完全沒有 —— 拿過來能填到我的空缺
    fills_me = [c for c in ids if _spare(theirs, c) and _missing(mine, c)]

    if fills_them and fills_me:
        return SeriesMatch(series, MUTUAL, fills_them, fills_me)

    # 以下兩種是單向的。單向仍然要付出一張同系列的多餘卡當對價，
    # 差別只在收下的一方本來就有那張卡。
    if fills_me:
        # 我當發起方：送出任何一張同系列的多餘卡，換我缺的
        my_spares = [c for c in ids if _spare(mine, c)]
        if my_spares:
            return SeriesMatch(series, INCOMING, my_spares, fills_me)
        return None

    if fills_them:
        # 對方當發起方：對方送出任何一張同系列的多餘卡，換他缺的
        their_spares = [c for c in ids if _spare(theirs, c)]
        if their_spares:
            return SeriesMatch(series, OUTGOING, fills_them, their_spares)
        return None

    return None


def match_one(
    mine: dict[str, int],
    theirs: dict[str, int],
    ids_by_series: dict[str, list[str]] | None = None,
) -> tuple[str, list[SeriesMatch], int, int] | None:
    """比對兩份收藏。回傳 (整體類型, 各系列結果, 我補幾張, 對方補幾張)。"""
    if ids_by_series is None:
        ids_by_series = _ids_by_series()

    results = [
        m
        for series, ids in ids_by_series.items()
        if (m := _series_match(series, ids, mine, theirs)) is not None
    ]
    if not results:
        return None

    # 整體類型取最好的那一個：只要任一系列能互利，這個對象就是互利對象
    kind = min((r.kind for r in results), key=lambda k: _KIND_RANK[k])

    gain = sum(len(r.i_get) for r in results if r.kind in (MUTUAL, INCOMING))
    helped = sum(len(r.i_give) for r in results if r.kind in (MUTUAL, OUTGOING))

    results.sort(key=lambda r: (_KIND_RANK[r.kind], r.series))
    return kind, results, gain, helped


def _ids_by_series() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c in cards.all_cards():
        out.setdefault(c.series, []).append(c.id)
    return out


def find_matches(
    viewer_tag: str,
    collections: dict[str, dict[str, int]],
    players: dict[str, dict],
    same_clan_only: bool = True,
) -> list[dict]:
    """對 viewer_tag 找出所有可換的對象。

    `players` 需含 name / clan_tag / clan_name。同一個人的其他小號一樣會被列入
    （小號之間只要同部落也能互換），只有 viewer 自己會被排除。
    """
    mine = collections.get(viewer_tag, {})
    my_clan = (players.get(viewer_tag) or {}).get("clan_tag")
    ids_by_series = _ids_by_series()

    out: list[Match] = []
    for tag, info in players.items():
        if tag == viewer_tag:
            continue
        same_clan = bool(my_clan) and info.get("clan_tag") == my_clan
        if same_clan_only and not same_clan:
            continue

        res = match_one(mine, collections.get(tag, {}), ids_by_series)
        if res is None:
            continue
        kind, series, gain, helped = res
        out.append(
            Match(
                tag=tag,
                name=info.get("name") or tag,
                clan_tag=info.get("clan_tag"),
                clan_name=info.get("clan_name"),
                same_clan=same_clan,
                kind=kind,
                series=series,
                gain=gain,
                help=helped,
            )
        )

    # 互利優先；同類別內先看我能補幾張，再看能幫對方多少；最後用 tag 讓排序穩定
    out.sort(key=lambda m: (_KIND_RANK[m.kind], -m.gain, -m.help, m.tag))
    return [m.as_dict() for m in out]
