"""실시간 도착 Provider 인터페이스.

지역마다 API 가 다르므로 Planner 는 이 ABC 만 안다. 서울 밖으로 확장할 때
구현체만 갈아끼우고 코어는 건드리지 않는다 (설계서 §8 원칙 2).
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from nowbus.cache import TTLCache
from nowbus.models import Arrival, Stop


class ArrivalProvider(ABC):
    def __init__(self, cache_ttl_sec: float = 30.0) -> None:
        self._cache: TTLCache[list[Arrival]] = TTLCache(cache_ttl_sec)

    @abstractmethod
    async def _fetch(self, stop: Stop) -> list[Arrival]:
        """정류장 1곳의 도착정보를 가져온다. 구현체가 채운다."""

    async def arrivals_at_stop(self, stop: Stop) -> list[Arrival]:
        cached = self._cache.get(stop.stop_id)
        if cached is not None:
            return cached
        arrivals = await self._fetch(stop)
        self._cache.set(stop.stop_id, arrivals)
        return arrivals

    async def arrivals_at_stops(self, stops: list[Stop]) -> dict[str, list[Arrival]]:
        """병렬 조회. **부분 실패를 허용한다.**

        정류장 8곳 중 1곳이 타임아웃 나도 나머지로 결과를 만든다. 예외를 그대로
        전파하면 정작 필요한 아침에 앱이 통째로 죽는다. 그래서 TaskGroup 이 아니라
        gather(return_exceptions=True) 를 쓴다 - TaskGroup 은 한 태스크가 죽으면
        형제 태스크를 취소해 버려서 부분 결과를 못 건진다.
        """
        if not stops:
            return {}
        results = await asyncio.gather(
            *(self.arrivals_at_stop(s) for s in stops), return_exceptions=True
        )
        out: dict[str, list[Arrival]] = {}
        for stop, res in zip(stops, results, strict=True):
            if isinstance(res, BaseException):
                continue  # 이 정류장만 버린다
            out[stop.stop_id] = res
        return out
