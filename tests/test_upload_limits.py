"""請求大小與圖片尺寸的上限。

這兩道是「平常永遠不會觸發」的防線，所以特別容易在重構時被安靜地拆掉。
每個測試釘的是**當初做那個決定的理由**，不只是當下的數字。
"""

import os

import pytest

import app_factory
from services import importer

# ── 請求大小 ───────────────────────────────────────────────────────


def test_超大的_api_請求在認證之前就被擋掉(client):
    """413 要比 401 早發生。

    順序反過來的話，未登入者送來的超大 body 仍然會被完整讀進記憶體才發現
    不該理他 —— 那正是這道防線要避免的事。這裡刻意**不帶 cookie**：
    回 413 而不是 401，就證明了擋下來的時候還沒走到相依注入。
    """
    res = client.put("/api/collection", content=b"x" * (app_factory.MAX_API_BODY_BYTES + 1))
    assert res.status_code == 413


def test_截圖上傳有自己較寬鬆的上限(client):
    """一般 API 的 256KB 對截圖來說太小，但也不能因此把全站都放寬。"""
    body = b"x" * (app_factory.MAX_API_BODY_BYTES + 1)
    assert client.post("/api/import/screenshots", content=body).status_code != 413


def test_上傳上限是從_importer_的常數推導出來的():
    """兩邊各寫各的話，改了 MAX_IMAGES 之後這道上限會安靜地失去意義。"""
    floor = importer.MAX_IMAGES * importer.MAX_IMAGE_BYTES
    assert floor < app_factory.MAX_IMPORT_BODY_BYTES < floor * 1.1


# ── 圖片尺寸 ───────────────────────────────────────────────────────


def test_像素上限在_cv2_載入之前就設好了():
    """設定順序錯了不會有任何錯誤訊息，只是安靜地維持 OpenCV 的預設值
    （2³⁰ 像素，等於單張可以吃掉 3.2GB）。這裡直接問 cv2 本人。"""
    import cv2
    import numpy as np

    cap = int(os.environ["OPENCV_IO_MAX_IMAGE_PIXELS"])
    assert cap == 24_000_000

    side = int(cap**0.5) + 200
    ok, buf = cv2.imencode(".png", np.zeros((side, side), np.uint8))
    assert ok
    with pytest.raises(cv2.error):
        cv2.imdecode(np.frombuffer(buf.tobytes(), np.uint8), cv2.IMREAD_COLOR)


def test_整批解碼後的記憶體天花板還在預算內():
    """釘住的是那道乘法，不是兩個數字。

    analyze() 會同時持有整批解碼後的點陣圖，所以真正的上限是
    MAX_IMAGES x 像素上限 x 3。這台機器只有 7GB 而且要跟 NVR 共用，
    預算抓 600MB。任何一邊被調大都會在這裡被擋下來。
    """
    cap = int(os.environ["OPENCV_IO_MAX_IMAGE_PIXELS"])
    worst_case = importer.MAX_IMAGES * cap * 3
    assert worst_case <= 600 * 1024 * 1024, f"最壞情況 {worst_case / 1e6:.0f} MB 超出預算"


def test_壓縮炸彈被擋成一句友善的拒絕而不是_500():
    """檔案大小擋不住這種攻擊：PNG 對單色區域的壓縮比實測約 1:925。

    重點在「以什麼形式失敗」—— OpenCV 超過上限時是**丟 cv2.error 不是回
    None**，所以只寫 `if img is None` 的話這裡會變成未處理例外。
    """
    import cv2
    import numpy as np

    cap = int(os.environ["OPENCV_IO_MAX_IMAGE_PIXELS"])
    side = int(cap**0.5) + 200
    ok, buf = cv2.imencode(".png", np.zeros((side, side), np.uint8))
    assert ok
    bomb = buf.tobytes()
    # 前提確認：它確實小到通過檔案大小檢查，卻大到足以耗盡記憶體。
    assert len(bomb) < importer.MAX_IMAGE_BYTES

    entry = importer.analyze([("bomb.png", bomb)])["files"][0]
    assert entry["ok"] is False
    assert "尺寸過大" in entry["reason"]


def test_畫面上的張數跟後端同一個來源(client):
    """這條是實際踩過才補的：MAX_IMAGES 從 12 改成 8，畫面上那句
    「一次最多 12 張」沒跟著改，使用者要等到上傳被拒絕才發現。

    所以檢查兩件事 —— API 有把數字送出來，而且 HTML 裡沒有寫死任何數字。
    """
    import re

    import config

    assert client.get("/api/import/available").json()["max_images"] == importer.MAX_IMAGES

    html = (config.BASE_DIR / "web" / "index.html").read_text(encoding="utf-8")
    assert '<span id="importMax"></span>' in html, "填數字的位置不見了"
    assert not re.search(r"最多\s*\d+\s*張", html), "HTML 又把張數寫死了"

    # id 打錯的話畫面會永遠空白，而且不會有任何錯誤。
    js = (config.BASE_DIR / "web" / "app.js").read_text(encoding="utf-8")
    assert "#importMax" in js


def test_一次最多八張(client):
    # 張數檢查在路由的函式本體裡，而相依（登入）先於本體解析 —— 沒登入的話
    # 只會拿到 401，這個測試就等於什麼都沒測到。
    assert client.post("/api/players/verify", json={"tag": "#AAA", "token": "goodtok"}).status_code == 200

    files = [("files", (f"{i}.png", b"x", "image/png")) for i in range(importer.MAX_IMAGES + 1)]
    res = client.post("/api/import/screenshots", files=files)
    if res.status_code == 501:
        pytest.skip("這台沒裝 opencv")
    assert res.status_code == 400
    assert "最多" in res.json()["detail"]
