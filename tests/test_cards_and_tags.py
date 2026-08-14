"""卡表結構與標籤正規化。"""

import pytest

from core import cards, tags


def test_卡表共60張():
    assert len(cards.all_cards()) == 60


def test_四個系列的張數符合遊戲內顯示():
    # 截圖實測：聖水 19 / 闇黑 13 / 建築大師基地 11 / 超級部隊 17
    expected = {"elixir": 19, "dark": 13, "builder": 11, "super": 17}
    for key, n in expected.items():
        assert sum(1 for c in cards.all_cards() if c.series == key) == n, key


def test_卡片順序即相簿順序():
    ids = [c.id for c in cards.all_cards()]
    assert ids[0] == "elixir-01"
    assert ids[18] == "elixir-19"
    assert ids[19] == "dark-01"   # 第 4 列第 2 格，截圖核對過
    assert ids[32] == "builder-01"  # 第 6 列第 3 格
    assert ids[43] == "super-01"    # 第 8 列第 2 格
    assert ids[59] == "super-17"


def test_同系列判定():
    assert cards.same_series("elixir-01", "elixir-19")
    assert not cards.same_series("elixir-01", "dark-01")
    assert not cards.same_series("elixir-01", "不存在")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("#9QRUL2CVJ", "#9QRUL2CVJ"),
        ("9qrul2cvj", "#9QRUL2CVJ"),
        ("  #9QRUL2CVJ  ", "#9QRUL2CVJ"),
        ("#9qrul2cvj", "#9QRUL2CVJ"),
        ("9QRUL2CVO", "#9QRUL2CV0"),  # 使用者常把 0 看成 O
    ],
)
def test_標籤正規化(raw, expected):
    assert tags.normalize(raw) == expected


def test_空標籤被拒絕():
    with pytest.raises(ValueError):
        tags.normalize("")
    with pytest.raises(ValueError):
        tags.normalize("###")


def test_編碼後的井號():
    # 未編碼的 # 打 CoC API 會回 404 而不是錯誤訊息（SPEC §8）
    assert tags.encode("#9QRUL2CVJ") == "%239QRUL2CVJ"
