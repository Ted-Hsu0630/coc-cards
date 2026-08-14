"""靜態資源版本注入（避免部署後使用者拿到舊的前端）。

做法與 All-in-One Downloader 相同：HTML 原始檔不寫版本號，送出時才注入 mtime。
"""

import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

import config
from core.http_utils import inject_asset_versions

WEB = config.BASE_DIR / "web"


def mtime(name: str) -> int:
    return int((WEB / name).stat().st_mtime)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", pathlib.Path(tempfile.mkdtemp()) / "t.db")

    from services import coc

    async def noop():
        return None

    monkeypatch.setattr(coc, "startup", noop)
    monkeypatch.setattr(coc, "shutdown", noop)

    from app_factory import create_app

    with TestClient(create_app()) as c:
        yield c


def test_首頁注入目前的資源版本(client):
    html = client.get("/").text
    assert f'/static/style.css?v={mtime("style.css")}' in html
    assert f'/static/app.js?v={mtime("app.js")}' in html


def test_圖片也帶版本(client):
    html = client.get("/").text
    assert f'/static/img/token-1-settings.jpg?v={mtime("img/token-1-settings.jpg")}' in html


def test_首頁本身不可被快取(client):
    r = client.get("/")
    assert r.headers["cache-control"] == "no-cache, no-store, must-revalidate"


def test_靜態檔要求重新驗證(client):
    r = client.get(f'/static/app.js?v={mtime("app.js")}')
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_api_回應不可被快取(client):
    assert client.get("/api/cards").headers["cache-control"] == "no-store"
    assert client.get("/api/me").headers["cache-control"] == "no-store"


def test_原始檔不可自己寫死版本號():
    """版本號由伺服器注入。原始碼裡手寫 ?v= 會讓注入失效或疊加。"""
    for name in ("index.html", "app.js", "style.css"):
        assert "?v=" not in (WEB / name).read_text(encoding="utf-8"), name


def test_版本號改變時舊的會被取代():
    html = '<script src="/static/app.js?v=111"></script>'
    out = inject_asset_versions(html, WEB)
    assert "?v=111" not in out
    assert f'?v={mtime("app.js")}' in out


def test_找不到的檔案不改寫URL():
    html = '<script src="/static/nope.js"></script>'
    assert inject_asset_versions(html, WEB) == html
