"""장소 검색(지오코딩). 좌표를 사용자 대신 프로그램이 구한다 [F-19].

왜 필요했나: 즐겨찾기를 등록하려면 '위도,경도'를 직접 입력해야 했다. 일반적으로
사용자는 자기 집 좌표를 모른다. 지도앱에서 좌표를 캐내 복사해 오는 절차는
'아침에 아이콘 한 번 탭'이라는 이 앱의 전제와 맞지 않는다.

두 공급원을 합쳐 쓴다.

1. StopGeocoder - 이미 DB 에 있는 정류장 12,897곳의 이름. 키도 네트워크도
   필요 없고 즉시 응답한다. 서울 정류장 이름은 대부분 랜드마크 기반이라
   ("시청", "롯데백화점", "○○아파트") 이것만으로도 상당 부분 커버된다.
2. KakaoGeocoder - 카카오 로컬 API. 건물명·상호·주소까지 찾는다. REST 키가
   있을 때만 활성화된다. 없으면 1번만 돌고, 이는 정상 동작이다.

정류장 좌표를 장소로 쓸 때의 함정: 승차 정류장 좌표를 그대로 출발지로 저장하면
도보 시간이 0 으로 잡혀 탑승 가능성 판정이 무의미해진다. 그래서 StopGeocoder
결과에는 source="stop" 을 달아 프론트가 '정류장 위치예요'라고 경고할 수 있게 한다.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import replace
from itertools import zip_longest

import httpx

from nowbus.core.walking import haversine_m
from nowbus.db.repo import StaticRepo
from nowbus.models import PlaceHit

Coord = tuple[float, float]

KAKAO_BASE = "https://dapi.kakao.com/v2/local/search"


class Geocoder(ABC):
    @abstractmethod
    async def search(self, query: str, limit: int = 8, near: Coord | None = None) -> list[PlaceHit]:
        """장소 이름/주소 → 좌표 후보. near 가 있으면 가까운 순으로 정렬한다."""


def _with_distance(hits: list[PlaceHit], near: Coord | None) -> list[PlaceHit]:
    """near 기준 거리를 채운다. 정렬은 하지 않는다 - 공급원별 관련도가 먼저다."""
    if near is None:
        return hits
    return [
        replace(h, distance_m=haversine_m(near[0], near[1], h.lat, h.lon))
        if h.distance_m is None
        else h
        for h in hits
    ]


class StopGeocoder(Geocoder):
    """DB 에 적재된 정류장 이름 검색. 네트워크 없음."""

    def __init__(self, repo: StaticRepo) -> None:
        self.repo = repo

    async def search(self, query: str, limit: int = 8, near: Coord | None = None) -> list[PlaceHit]:
        found = self.repo.search_stops(query, limit)
        hits = [
            PlaceHit(
                name=stop.name,
                lat=stop.lat,
                lon=stop.lon,
                address=f"버스 정류장 · 노선 {n}개",
                source="stop",
                category="정류장",
            )
            for stop, n in found
        ]
        return _with_distance(hits, near)


class KakaoGeocoder(Geocoder):
    """카카오 로컬 API. 키워드(상호·건물명) + 주소 두 질의를 같이 던진다.

    두 엔드포인트 모두 **실물 응답으로 확인했다** (2026-09-19, 사용자 로컬).
    documents 의 키는 정확히 이랬다.
      keyword: address_name, category_group_code, category_group_name,
        category_name, distance, id, phone, place_name, place_url,
        road_address_name, x, y
      address: address, address_name, address_type, road_address, x, y

    개발 컨테이너에서는 dapi.kakao.com 이 조직 이그레스 정책에 막혀 있으므로
    확인은 로컬에서 한다:

        python scripts/smoke_geocode.py "세종대로 110"

    이 스크립트가 원본 응답을 tests/fixtures/{keyword,address}_kakao.json 에
    저장한다. 응답이 다르면 그 픽스처를 갱신하고 _parse_* 를 고칠 것.
    추측으로 고치지 말 것 (인계 메모 §3 과 같은 이유다).
    """

    def __init__(self, rest_key: str, client: httpx.AsyncClient) -> None:
        self.rest_key = rest_key
        self.client = client

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"KakaoAK {self.rest_key}"}

    async def search(self, query: str, limit: int = 8, near: Coord | None = None) -> list[PlaceHit]:
        if not self.rest_key:
            return []
        # 상호/건물명과 주소는 엔드포인트가 다르다. 사용자가 어느 쪽을 입력했는지
        # 알 수 없으니 둘 다 던지고 합친다. 한쪽이 실패해도 나머지는 살린다
        # (도착정보 병렬 조회와 같은 원칙 - 아침에 통째로 죽으면 안 된다).
        results = await asyncio.gather(
            self._keyword(query, limit, near),
            self._address(query, limit),
            return_exceptions=True,
        )
        hits: list[PlaceHit] = []
        for res in results:
            if isinstance(res, BaseException):
                continue
            hits.extend(res)
        return _with_distance(hits, near)

    async def _get(self, path: str, params: dict[str, str | int | float]) -> dict:
        r = await self.client.get(
            f"{KAKAO_BASE}/{path}", params=params, headers=self.headers, timeout=3.0
        )
        r.raise_for_status()
        return r.json()

    async def _keyword(self, query: str, limit: int, near: Coord | None) -> list[PlaceHit]:
        params: dict[str, str | int | float] = {"query": query, "size": min(limit, 15)}
        if near is not None:
            # x=경도, y=위도. 카카오는 이 순서다.
            # sort=distance 는 주지 않는다. 목적지를 검색하는데 현재 위치에서
            # 가까운 순으로 정렬하면 '내가 말한 그 스타벅스'가 뒤로 밀린다.
            # 관련도 순(기본값)을 그대로 쓰고, 거리는 우리가 직접 계산해 붙인다.
            # 반경(radius)도 주지 않는다 - 반경 밖 목적지를 지워버린다.
            params |= {"x": near[1], "y": near[0]}
        return _parse_kakao(await self._get("keyword.json", params), "kakao-keyword")

    async def _address(self, query: str, limit: int) -> list[PlaceHit]:
        params: dict[str, str | int | float] = {"query": query, "size": min(limit, 15)}
        return _parse_kakao(await self._get("address.json", params), "kakao-address")


def _parse_kakao(body: dict, source: str) -> list[PlaceHit]:
    """카카오 로컬 응답 → PlaceHit.

    keyword.json 과 address.json 의 문서가 서로 다르다.
      keyword: place_name, road_address_name, address_name,
        category_group_name, category_name, distance, id, phone, place_url, x, y
      address: address_name, address_type, road_address{address_name},
        address{region_3depth_name}, x, y
    공통으로 x(경도)·y(위도)가 문자열로 온다.

    없는 키는 전부 None 으로 흘린다. 좌표를 못 읽은 항목만 버린다.
    """
    out: list[PlaceHit] = []
    for doc in body.get("documents") or []:
        try:
            lon = float(doc["x"])
            lat = float(doc["y"])
        except (KeyError, TypeError, ValueError):
            continue
        name = doc.get("place_name") or doc.get("address_name") or ""
        if not name:
            continue
        address = doc.get("road_address_name") or doc.get("address_name")
        if isinstance(doc.get("road_address"), dict):
            address = doc["road_address"].get("address_name") or address
        raw_distance = doc.get("distance")
        try:
            distance = float(raw_distance) if raw_distance else None
        except (TypeError, ValueError):
            distance = None
        out.append(
            PlaceHit(
                name=name,
                lat=lat,
                lon=lon,
                address=address,
                source=source,
                category=(doc.get("category_group_name") or doc.get("category_name") or None),
                distance_m=distance,
            )
        )
    return out


class ChainGeocoder(Geocoder):
    """여러 공급원을 합친다. 한 곳이 목록을 독점하지 못하게 번갈아 담는다.

    앞쪽 공급원부터 순서대로 다 담으면 안 된다. 정류장 검색이 먼저인데 "강남"
    같은 질의는 정류장 이름만으로 8개가 차서 카카오 결과가 화면에 못 올라온다.
    그러면 건물·상호 검색을 붙인 의미가 없다. 그래서 공급원별로 하나씩 번갈아
    담는다 - 공급원마다 자기 관련도 순서는 유지되고, 어느 쪽도 사라지지 않는다.
    """

    def __init__(self, sources: list[Geocoder]) -> None:
        self.sources = sources

    async def search(self, query: str, limit: int = 8, near: Coord | None = None) -> list[PlaceHit]:
        results = await asyncio.gather(
            *(src.search(query, limit, near) for src in self.sources),
            return_exceptions=True,
        )
        lists = [r for r in results if not isinstance(r, BaseException)]  # 죽은 곳은 버린다

        seen: set[tuple[float, float, str]] = set()
        out: list[PlaceHit] = []
        for row in zip_longest(*lists):
            for h in row:
                if h is None or len(out) >= limit:
                    continue
                # 약 11m 격자. 같은 건물이 상호만 다르게 여러 번 오는 걸 줄인다.
                key = (round(h.lat, 4), round(h.lon, 4), h.name)
                if key in seen:
                    continue
                seen.add(key)
                out.append(h)
        return out[:limit]


def usable_kakao_key(raw: str) -> str:
    """헤더에 넣어도 되는 키만 통과시킨다. 아니면 빈 문자열.

    HTTP 헤더는 ASCII 만 담을 수 있어서, 한글이 섞인 값을 그대로 넘기면 httpx 가
    헤더를 만들다 UnicodeEncodeError 를 던진다. .env 에 안내문의 자리표시자를
    그대로 넣은 사례가 실제로 있었다. 그때 요청 경로에서 터지게 두지 않고
    '카카오 없이 정류장 검색만' 으로 떨어뜨린다 - 검색이 통째로 죽는 것보다 낫다.
    공백은 따옴표째 붙여넣은 경우라 함께 걸러낸다.
    """
    key = (raw or "").strip()
    if not key or not key.isascii() or any(c.isspace() for c in key):
        return ""
    return key


def build_geocoder(
    repo: StaticRepo, kakao_rest_key: str, client: httpx.AsyncClient | None
) -> Geocoder:
    """정류장 검색은 항상, 카카오는 쓸 만한 키와 클라이언트가 있을 때만."""
    sources: list[Geocoder] = [StopGeocoder(repo)]
    key = usable_kakao_key(kakao_rest_key)
    if key and client is not None:
        sources.append(KakaoGeocoder(key, client))
    return ChainGeocoder(sources)
