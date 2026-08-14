"""避免瀏覽器拿到舊的前端檔案。

做法與 All-in-One Downloader 的 `app/web/http_utils.py` 相同：
HTML 原始檔保持乾淨，**送出時**才即時把 `?v=<mtime>` 注入到靜態資源的 URL。
這樣改前端不必手動維護版本號，檔案一存檔 URL 就變了，瀏覽器自然重抓。

單靠 `Cache-Control: no-cache` 不夠：那只保證「用之前先問伺服器」，
而部署後使用者常常整天不重新整理，手機瀏覽器也可能整個略過重驗證。
換掉 URL 才是真正強制更新的手段。
"""

import re
from pathlib import Path

from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}

# 圖片也要納入：教學截圖重新編碼過一次，內容變了但檔名沒變，
# 沒有版本號的話使用者會一直看到舊的那張。
_ASSET_VERSION_RE = re.compile(
    r"(?P<path>/static/[\w./-]+\.(?:css|js|jpg|jpeg|png|svg|webp|ico))(?:\?v=[\w.-]+)?"
)


def _asset_version(url_path: str, static_dir: Path) -> str | None:
    """回傳 /static/... 檔案的 mtime；找不到檔案就不改寫 URL。"""
    asset = static_dir / url_path.removeprefix("/static/")
    try:
        return str(int(asset.stat().st_mtime))
    except OSError:
        return None


def inject_asset_versions(html: str, static_dir: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        version = _asset_version(match.group("path"), static_dir)
        return f"{match.group('path')}?v={version}" if version else match.group(0)

    return _ASSET_VERSION_RE.sub(replace, html)


def no_cache_page(path: Path, static_dir: Path) -> FileResponse | HTMLResponse:
    """送出頁面並即時注入資源版本；HTML 本身一律要求重新驗證。"""
    if path.suffix == ".html":
        return HTMLResponse(
            inject_asset_versions(path.read_text(encoding="utf-8"), static_dir),
            headers=NO_CACHE_HEADERS,
        )
    return FileResponse(path, headers=NO_CACHE_HEADERS)


class RevalidatedStaticFiles(StaticFiles):
    """讓靜態檔即使被直接引用（沒帶版本號）也會回來重新驗證。"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response
