"""「加到主畫面」的那組設定。

這些東西壞掉的方式很安靜：manifest 少一個欄位、圖示尺寸標錯、路徑打錯，
瀏覽器都只是**默默不給裝**，畫面上一切正常。只有真的拿手機去加一次才會發現，
所以這裡把每個會讓安裝失敗的前提都釘住。

刻意不裝 Pillow 讀圖（不在專案相依裡，CI 沒有），PNG 的寬高就在檔頭固定位置，
自己讀 25 個 byte 就夠了。
"""

import json
import re
import struct

import pytest

import config

WEB = config.BASE_DIR / "web"
MANIFEST = WEB / "manifest.webmanifest"


def _png_header(path):
    """回傳 (寬, 高, 有沒有 alpha)。

    PNG 規格：8 byte 簽章 + 4 byte 長度 + b"IHDR" + 寬 + 高 + 位元深度 + 色彩型別。
    色彩型別 4（灰階+A）與 6（RGB+A）帶 alpha。
    """
    head = path.read_bytes()[:26]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} 不是 PNG"
    assert head[12:16] == b"IHDR", f"{path.name} 的第一個 chunk 不是 IHDR"
    w, h = struct.unpack(">II", head[16:24])
    return w, h, head[25] in (4, 6)


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_送得出來而且是_json(client):
    """放在 /static 底下靠的是 StaticFiles，副檔名沒被認得就會回錯的型別。"""
    res = client.get("/static/manifest.webmanifest")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/manifest+json")
    assert res.json()["display"] == "standalone"


def test_可安裝的最低條件都在(manifest):
    """少任何一項，瀏覽器就是不給裝，而且不會說為什麼。

    `start_url` 與 `scope` 是相對於 **manifest 自己的位置**解析的，而這份
    manifest 在 /static/ 底下 —— 寫成 "." 或 "./" 會變成 /static/，
    使用者從主畫面點開會落在靜態目錄而不是首頁。所以只能是絕對路徑。
    """
    for key in ("name", "short_name", "start_url", "scope", "display", "icons"):
        assert manifest.get(key), f"manifest 少了 {key}"
    assert manifest["start_url"].startswith("/"), "start_url 必須是絕對路徑"
    assert manifest["scope"].startswith("/"), "scope 必須是絕對路徑"
    # 主畫面圖示底下的字，太長會被系統截掉
    assert len(manifest["short_name"]) <= 12


def test_圖示宣告的尺寸跟檔案實際尺寸一致(manifest):
    """最容易腐爛的一條：換了圖但 sizes 忘了改，Chrome 會直接忽略那個圖示。"""
    for icon in manifest["icons"]:
        path = WEB / icon["src"].removeprefix("/static/")
        assert path.exists(), f"{icon['src']} 不存在"
        w, h, _ = _png_header(path)
        assert f"{w}x{h}" == icon["sizes"], f"{icon['src']} 實際是 {w}x{h}"


def test_any_與_maskable_兩種都要有(manifest):
    """maskable 是給 Android 套自己的形狀用的（圓形、水滴、方形都可能），
    保證看得到的只有中心 80% 直徑的圓。

    只給 any 的話 Android 不會直接用，而是自己加一圈白底再把圖縮小 ——
    臉會變得很小，而且白框跟深色主題完全不搭。反過來只給 maskable 也不行，
    分頁跟 Windows 是照原樣畫成正方形，會顯得留白過多。
    """
    by_purpose = {}
    for icon in manifest["icons"]:
        by_purpose.setdefault(icon.get("purpose", "any"), set()).add(icon["sizes"])
    # 192 與 512 是 Chrome 判定可安裝的門檻
    assert {"192x192", "512x512"} <= by_purpose.get("any", set())
    assert {"192x192", "512x512"} <= by_purpose.get("maskable", set())


def test_圖示不可以有透明通道(manifest):
    """透明的地方會被系統填成白色 —— 深色主題的圖示上出現白塊。"""
    for icon in manifest["icons"]:
        _, _, has_alpha = _png_header(WEB / icon["src"].removeprefix("/static/"))
        assert not has_alpha, f"{icon['src']} 帶 alpha"


def test_首頁有把_manifest_跟_ios_圖示接上():
    """manifest 寫得再對，沒有這幾行也等於不存在。

    iOS 16.4 以前不看 manifest，apple-touch-icon 是它唯一的來源；
    少了它 iOS 會自己截一張網頁縮圖當圖示。
    """
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="/static/manifest.webmanifest">' in html
    assert 'rel="apple-touch-icon"' in html
    assert 'name="theme-color"' in html
    assert (WEB / "img" / "apple-touch-icon.png").exists()


def test_ios_狀態列不可以用_black_translucent():
    """black-translucent 會讓內容鑽到狀態列底下。

    這個站的 safe-area 內距只加在 #topbar 上，而登入畫面**沒有** topbar，
    標題會直接被時鐘壓到。要改成 translucent 的話得先處理登入畫面的上緣。
    """
    html = (WEB / "index.html").read_text(encoding="utf-8")
    # 比對 meta 的 content 而不是整份 HTML —— 上面那段註解本來就會提到這個字。
    m = re.search(r'<meta\s+name="apple-mobile-web-app-status-bar-style"\s+content="([^"]+)"', html)
    assert m, "找不到 apple-mobile-web-app-status-bar-style"
    assert m.group(1) == "black"
