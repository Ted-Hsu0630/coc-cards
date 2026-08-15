"""所有測試共用的前置。"""

import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """接上假的 CoC API 的 TestClient。

    `test_http_flow.py` 有自己的同名 fixture（多了查無標籤的案例），
    模組層級的定義會蓋過這裡，兩邊互不影響。

    順帶一提，建立 app 會連帶初始化 `services` 套件，也就是在任何
    `import cv2` 之前把像素上限設好（見 services/__init__.py）。
    """
    import config

    monkeypatch.setattr(config, "DB_PATH", pathlib.Path(tempfile.mkdtemp()) / "t.db")
    monkeypatch.setattr(config, "COOKIE_SECURE", False)

    from services import coc

    async def verify_token(tag, token):
        return token == "goodtok"

    async def get_player(tag):
        return {"tag": tag, "name": f"村莊{tag}", "clan_tag": "#C1", "clan_name": "天堂"}

    async def noop():
        return None

    monkeypatch.setattr(coc, "verify_token", verify_token)
    monkeypatch.setattr(coc, "get_player", get_player)
    monkeypatch.setattr(coc, "startup", noop)
    monkeypatch.setattr(coc, "shutdown", noop)

    from app_factory import create_app

    with TestClient(create_app()) as c:
        yield c
