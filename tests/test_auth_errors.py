"""驗證失敗時要能分辨「標籤打錯」與「權杖打錯」。

CoC 的 verifytoken 對不存在的標籤也回 200 + status=invalid（實測），
所以不多查一次的話，兩種錯誤會給出同一句話，標籤打錯的人會一直去重抓權杖。
"""

import pytest

from core import db
from services import auth, coc


@pytest.fixture
def conn(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "t.db")
    db.init()
    with db.session() as c:
        yield c


@pytest.fixture
def fake_coc(monkeypatch):
    def _install(*, verify_ok, player_exists):
        async def verify_token(tag, token):
            return verify_ok

        async def get_player(tag):
            if not player_exists:
                raise coc.PlayerNotFound("查無此標籤")
            return {"tag": tag, "name": "測試員", "clan_tag": "#C1", "clan_name": "測試部落"}

        monkeypatch.setattr(coc, "verify_token", verify_token)
        monkeypatch.setattr(coc, "get_player", get_player)

    return _install


async def test_標籤不存在時回報標籤問題而非權杖問題(conn, fake_coc):
    fake_coc(verify_ok=False, player_exists=False)
    with pytest.raises(coc.PlayerNotFound):
        await auth.verify_and_bind(conn, "#ZZZZZZZZZ", "00000000", None)


async def test_標籤存在但權杖錯時明確指出是權杖(conn, fake_coc):
    fake_coc(verify_ok=False, player_exists=True)
    with pytest.raises(auth.VerificationFailed, match="權杖"):
        await auth.verify_and_bind(conn, "#9QRUL2CVJ", "00000000", None)


async def test_驗證成功會建立帳號與村莊(conn, fake_coc):
    fake_coc(verify_ok=True, player_exists=True)
    r = await auth.verify_and_bind(conn, "9qrul2cvj", "goodtok", None)
    assert r["tag"] == "#9QRUL2CVJ"          # 標籤有被正規化
    assert r["player"]["clan_name"] == "測試部落"
    row = conn.execute("SELECT * FROM players WHERE tag = ?", ("#9QRUL2CVJ",)).fetchone()
    assert row["user_id"] == r["user_id"]


async def test_加綁小號掛在同一個帳號底下(conn, fake_coc):
    fake_coc(verify_ok=True, player_exists=True)
    first = await auth.verify_and_bind(conn, "#9QRUL2CVJ", "tok", None)
    second = await auth.verify_and_bind(conn, "#PJV0QCG9U", "tok", first["user_id"])
    assert second["user_id"] == first["user_id"]
    assert len(auth.players.players_of_user(conn, first["user_id"])) == 2


async def test_別人已綁的村莊不會被搶走(conn, fake_coc):
    from services.players import TagAlreadyBound

    fake_coc(verify_ok=True, player_exists=True)
    owner = await auth.verify_and_bind(conn, "#9QRUL2CVJ", "tok", None)
    with pytest.raises(TagAlreadyBound):
        await auth.verify_and_bind(conn, "#9QRUL2CVJ", "tok", owner["user_id"] + 999)
