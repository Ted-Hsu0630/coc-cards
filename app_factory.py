"""FastAPI 應用組裝。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
from core import cards, db
from routers import auth, collection, matches
from services import coc

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db.init()
    # 卡表結構壞掉會讓配對靜靜地算錯，寧可在啟動時就炸掉
    n = len(cards.all_cards())
    log.info("卡表載入 %d 張", n)
    await coc.startup()
    yield
    await coc.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="coc-cards", lifespan=lifespan, docs_url=None, redoc_url=None)

    app.include_router(auth.router)
    app.include_router(collection.router)
    app.include_router(matches.router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "cards": len(cards.all_cards())}

    web = config.BASE_DIR / "web"
    if web.is_dir():
        app.mount("/static", StaticFiles(directory=web), name="static")

        @app.get("/")
        def index():
            return FileResponse(web / "index.html")

    return app
