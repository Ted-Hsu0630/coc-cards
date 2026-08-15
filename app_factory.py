"""FastAPI 應用組裝。"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

import config
from core import cards, db
from core.http_utils import RevalidatedStaticFiles, no_cache_page
from routers import auth, collection, matches
from routers import importer as import_router
from services import auth as auth_service
from services import coc, players
from services import importer as importer_service

# 這個站的 API 除了截圖上傳以外都很小 —— 最大的 `PUT /api/collection` 也只是
# 60 個整數。設上限是為了讓惡意的超大 body 在讀進記憶體之前就被擋掉：這台機器
# 只有 7GB，而且要跟 camera-viewer 的 24 小時錄影共用。
MAX_API_BODY_BYTES = 256 * 1024

# 截圖上傳是唯一的例外。上限直接由 importer 的兩個常數推導，不要各寫各的 ——
# 分開寫的話改了一邊另一邊會安靜地失去意義。多留 64KB 給 multipart 的分隔線
# 與各段的標頭。
IMPORT_PATH = "/api/import/screenshots"
MAX_IMPORT_BODY_BYTES = (
    importer_service.MAX_IMAGES * importer_service.MAX_IMAGE_BYTES + 64 * 1024
)

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db.init()
    # 卡表結構壞掉會讓配對靜靜地算錯，寧可在啟動時就炸掉
    n = len(cards.all_cards())
    log.info("卡表載入 %d 張", n)

    # 每次部署都會重啟容器，所以這裡等於是定期清理
    with db.session() as conn:
        removed = auth_service.purge_expired_sessions(conn)
    if removed:
        log.info("清掉 %d 個過期 session", removed)

    await coc.startup()
    refresher = asyncio.create_task(_refresh_clans_forever())
    yield
    refresher.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await refresher
    await coc.shutdown()


async def _refresh_clans_forever() -> None:
    """在背景把部落資訊換新，不要讓使用者在請求裡等。

    以前同步是掛在 `/api/matches` 與 `/api/clan/overview` 裡的：快取一過期，
    下一個開頁面的人就要獨自等完整趟 CoC API（正式機實測 886ms，而且所有玩家
    共用同一個時間戳，等於每 10 分鐘固定有一個人被抓去付帳）。

    先睡再跑，理由有二：剛啟動時資料通常還新，而且測試的 TestClient 會觸發
    lifespan —— 間隔是分鐘級，測試跑幾秒鐘根本不會碰到這裡。
    路由裡那道呼叫刻意保留：這個迴圈死掉時它就是備援，只是使用者要等一下。
    """
    if config.CLAN_REFRESH_SECONDS <= 0:
        return
    while True:
        await asyncio.sleep(config.CLAN_REFRESH_SECONDS)
        try:
            with db.session() as conn:
                await players.sync_clans(conn)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 這裡炸掉不可以讓迴圈停掉 —— 停了就再也沒有背景更新，
            # 而且沒有任何跡象（頁面只會慢慢變舊）。
            log.exception("背景部落同步失敗，下一輪再試")


def create_app() -> FastAPI:
    app = FastAPI(title="coc-cards", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def limit_api_body(request: Request, call_next):
        """超過上限的請求在讀取 body 之前就回絕。

        只看 `Content-Length`，所以擋不住 chunked transfer-encoding（那種請求
        根本沒有這個標頭）。**這不是唯一的防線**，也不該是 —— 真正有界的保證
        在 `routers/importer.py` 逐檔 `read(MAX+1)` 與 `services/__init__.py`
        的像素上限那兩層。這一層的價值是讓最常見的攻擊在最便宜的地方就停下。

        解析不出來的 Content-Length 一律當成太大：合法的客戶端不會送出這種值。
        """
        if request.url.path.startswith("/api/"):
            limit = MAX_IMPORT_BODY_BYTES if request.url.path == IMPORT_PATH else MAX_API_BODY_BYTES
            raw = request.headers.get("content-length")
            if raw is not None:
                try:
                    too_large = int(raw) > limit
                except ValueError:
                    too_large = True
                if too_large:
                    return JSONResponse(
                        {"detail": "檔案太大"},
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    )
        return await call_next(request)

    @app.middleware("http")
    async def no_store_api(request: Request, call_next):
        response = await call_next(request)
        # 配對結果會隨別人更新收藏而變，被快取住就會看到過時的建議
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(auth.router)
    app.include_router(collection.router)
    app.include_router(matches.router)
    # 截圖辨識是選用功能：沒裝 opencv 時路由照樣掛著，
    # /api/import/available 會回 false，前端就不顯示那個分頁
    app.include_router(import_router.router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "cards": len(cards.all_cards())}

    web = config.BASE_DIR / "web"
    if web.is_dir():
        app.mount("/static", RevalidatedStaticFiles(directory=web), name="static")

        @app.get("/")
        def index():
            # 不直接回 FileResponse：要在送出時把 ?v=<mtime> 注入資源 URL，
            # 否則部署完使用者手上還是舊的 app.js（見 core/http_utils.py）
            return no_cache_page(web / "index.html", web)

    return app
