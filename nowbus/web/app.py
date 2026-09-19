"""FastAPI 인스턴스. 정적 파일 서빙과 수명주기만 담당한다."""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from nowbus.config import Settings
from nowbus.web.deps import build_provider
from nowbus.web.routes import router

STATIC_DIR = pathlib.Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    async with httpx.AsyncClient(timeout=settings.http_timeout_sec) as client:
        # Provider 는 앱 수명 동안 하나. TTL 캐시와 커넥션 풀을 공유해야 한다.
        app.state.client = client  # 장소 검색(지오코더)도 같은 커넥션 풀을 쓴다
        app.state.provider = build_provider(settings, client)
        yield


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="NowBus", lifespan=lifespan)
    app.state.settings = settings or Settings()
    app.include_router(router)
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
