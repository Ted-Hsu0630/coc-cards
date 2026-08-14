"""FastAPI 應用組裝。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

import config
from core import cards, db
from core.http_utils import RevalidatedStaticFiles, no_cache_page
from routers import auth, collection, matches
from services import auth as auth_service
from services import coc

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
    yield
    await coc.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="coc-cards", lifespan=lifespan, docs_url=None, redoc_url=None)

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
