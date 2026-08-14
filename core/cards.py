"""卡表載入與查詢。

卡表是靜態資料，程式啟動時讀一次即可。配對邏輯只依賴 id 與 series，
名字純粹是顯示用 —— 名字打錯不會讓配對出錯。
"""

import json
from functools import lru_cache

import config


class Card:
    __slots__ = ("id", "series", "index", "name_zh", "name_en", "confirmed")

    def __init__(self, id: str, series: str, index: int, name_zh: str, name_en: str, confirmed: bool):
        self.id = id
        self.series = series
        self.index = index
        self.name_zh = name_zh
        self.name_en = name_en
        self.confirmed = confirmed

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "series": self.series,
            "index": self.index,
            "name_zh": self.name_zh,
            "name_en": self.name_en,
            "confirmed": self.confirmed,
        }


@lru_cache(maxsize=1)
def _load() -> tuple[list[Card], dict, dict]:
    raw = json.loads(config.CARDS_PATH.read_text(encoding="utf-8"))
    series = {s["key"]: s for s in raw["series"]}

    cards: list[Card] = []
    for i, c in enumerate(raw["cards"]):
        key = c["id"].rsplit("-", 1)[0]
        if key not in series:
            raise ValueError(f"卡片 {c['id']} 的系列 {key!r} 不在 series 定義中")
        cards.append(Card(c["id"], key, i, c["name_zh"], c["name_en"], c["confirmed"]))

    # 卡表結構是配對正確性的前提，載入時就驗，不要等到配對算錯才發現
    expected = sum(s["count"] for s in raw["series"])
    if len(cards) != expected:
        raise ValueError(f"卡片數 {len(cards)} 與 series 宣告的總和 {expected} 不符")
    for key, s in series.items():
        n = sum(1 for c in cards if c.series == key)
        if n != s["count"]:
            raise ValueError(f"系列 {key} 有 {n} 張，宣告是 {s['count']} 張")
    if len({c.id for c in cards}) != len(cards):
        raise ValueError("卡片 id 有重複")

    by_id = {c.id: c for c in cards}
    return cards, by_id, series


def all_cards() -> list[Card]:
    return _load()[0]


def by_id(card_id: str) -> Card | None:
    return _load()[1].get(card_id)


def series_meta() -> dict:
    return _load()[2]


def same_series(a: str, b: str) -> bool:
    """SPEC §2.3：只能同系列互換。"""
    ca, cb = by_id(a), by_id(b)
    return ca is not None and cb is not None and ca.series == cb.series


def valid_ids() -> set[str]:
    return set(_load()[1])
