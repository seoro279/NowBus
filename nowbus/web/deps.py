"""의존성 주입과 토큰 인증.

Annotated 방식을 쓴다. 기본값 자리에 Depends() 를 두는 옛 스타일은 ruff B008 에
걸리고, 타입 별칭으로 빼두면 라우트 시그니처도 짧아진다.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends, Header, HTTPException, Query, Request, status

from nowbus.config import Settings
from nowbus.db.repo import StaticRepo
from nowbus.providers.base import ArrivalProvider
from nowbus.providers.seoul import SeoulProvider


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_repo(settings: SettingsDep) -> AsyncIterator[StaticRepo]:
    """요청마다 새 연결을 연다.

    sqlite3 연결은 만든 스레드에서만 쓸 수 있다. FastAPI 는 **동기** 의존성과
    핸들러를 스레드풀로 돌리므로, 동기로 두면 연결을 워커 스레드에서 만들고
    이벤트 루프에서 쓰게 되어 ProgrammingError 가 난다. 그래서 의존성도 라우트도
    전부 async 로 통일해 한 스레드에 묶는다. SQLite 조회는 마이크로초 단위라
    이벤트 루프를 막는 것도 문제되지 않는다.
    """
    repo = StaticRepo(settings.db_path)
    try:
        yield repo
    finally:
        repo.close()


def get_provider(request: Request) -> ArrivalProvider:
    """앱 수명 동안 하나만 둔다. TTL 캐시와 커넥션 풀을 요청 간에 공유해야 한다."""
    return request.app.state.provider


def require_token(
    settings: SettingsDep,
    x_token: Annotated[str | None, Header(alias="X-Token")] = None,
    token: Annotated[str | None, Query()] = None,
) -> None:
    """고정 토큰 하나. 개인용이므로 이 정도면 충분하다.

    헤더와 쿼리스트링을 모두 받는다. 프론트는 헤더를 쓰고, curl 로 찔러볼 때는
    쿼리가 편하다. 비교는 상수 시간으로 한다.
    """
    supplied = x_token or token or ""
    if not secrets.compare_digest(supplied, settings.api_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "토큰이 틀렸다")


RepoDep = Annotated[StaticRepo, Depends(get_repo)]
ProviderDep = Annotated[ArrivalProvider, Depends(get_provider)]
TokenDep = Annotated[None, Depends(require_token)]


def build_provider(settings: Settings, client: httpx.AsyncClient) -> ArrivalProvider:
    return SeoulProvider(settings.seoul_api_key, client, cache_ttl_sec=settings.cache_ttl_sec)
