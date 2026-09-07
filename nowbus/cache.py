"""TTL 인메모리 캐시. 실시간 도착정보 중복 호출을 막는다 (F-10)."""

from __future__ import annotations

import time
from typing import Generic, TypeVar

V = TypeVar("V")


class TTLCache(Generic[V]):
    """개인용 단일 프로세스 전제라 잠금 없이 dict 하나로 충분하다."""

    def __init__(self, ttl_sec: float) -> None:
        self.ttl = ttl_sec
        self._data: dict[str, tuple[float, V]] = {}

    def get(self, key: str) -> V | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if time.monotonic() >= expires_at:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: V) -> None:
        self._data[key] = (time.monotonic() + self.ttl, value)

    def clear(self) -> None:
        self._data.clear()
