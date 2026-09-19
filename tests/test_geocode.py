"""장소 검색. 좌표를 사용자가 아니라 프로그램이 구한다 [F-19].

KEYWORD_SAMPLE 의 키 구성은 **실물 응답과 일치한다** (2026-09-19 로컬 확인).
값만 픽스처용으로 바꿨다. ADDRESS_SAMPLE 은 아직 문서 기준이다 - 확인 당시
질의가 주소가 아니어서 documents 가 0건이었다.

개발 컨테이너에서는 dapi.kakao.com 이 막혀 있어 여기서 실물을 받을 수 없다.
확인은 로컬에서 scripts/smoke_geocode.py 로 하고, 응답이 다르면 이 샘플과
파서를 같이 고친다.
"""

import httpx
import pytest

from nowbus.db.repo import StaticRepo, init_schema
from nowbus.models import PlaceHit
from nowbus.providers.geocode import (
    ChainGeocoder,
    Geocoder,
    KakaoGeocoder,
    StopGeocoder,
    _parse_kakao,
    build_geocoder,
    usable_kakao_key,
)
from nowbus.providers.seoul import parse_route_stops

SANGGYE_NAME = "상계주공7단지"

KEYWORD_SAMPLE = {
    "documents": [
        {
            "place_name": "스타벅스 강남역중앙점",
            "category_name": "음식점 > 카페 > 커피전문점 > 스타벅스",
            "category_group_name": "카페",
            "address_name": "서울 강남구 역삼동 821",
            "road_address_name": "서울 강남구 강남대로 390",
            "x": "127.0276",
            "y": "37.4979",
            "distance": "412",
            # 파서가 쓰지 않지만 실물에 오는 키들. 늘어도 깨지지 않는지 같이 고정한다.
            "id": "1234567890",
            "phone": "02-0000-0000",
            "place_url": "http://place.map.kakao.com/1234567890",
            "category_group_code": "CE7",
        }
    ],
    "meta": {"total_count": 1},
}

ADDRESS_SAMPLE = {
    "documents": [
        {
            "address_name": "서울 중구 세종대로 110",
            "x": "126.9780",
            "y": "37.5665",
            "road_address": {"address_name": "서울 중구 세종대로 110"},
            "address": {"region_3depth_name": "태평로1가"},
        }
    ],
    "meta": {"total_count": 1},
}


@pytest.fixture
def repo(fx):
    r = StaticRepo(":memory:")
    init_schema(r.conn)
    r.upsert_route_stops(parse_route_stops(fx("getStaionByRoute")))
    yield r
    r.close()


class TestStopGeocoder:
    """키 없이 동작하는 기본 공급원. DB 에 이미 12,897곳이 있다."""

    async def test_finds_a_stop_by_partial_name(self, repo):
        hits = await StopGeocoder(repo).search("상계주공")
        assert hits
        assert all("상계주공" in h.name for h in hits)
        assert all(h.source == "stop" for h in hits)

    async def test_returns_usable_coordinates(self, repo):
        hit = (await StopGeocoder(repo).search(SANGGYE_NAME))[0]
        assert 37.0 < hit.lat < 38.0
        assert 126.0 < hit.lon < 128.0

    async def test_fills_distance_when_near_is_given(self, repo):
        near = (37.5665, 126.9780)  # 시청
        hit = (await StopGeocoder(repo).search(SANGGYE_NAME, near=near))[0]
        assert hit.distance_m is not None and hit.distance_m > 1000

    async def test_unknown_name_is_empty_not_an_error(self, repo):
        assert await StopGeocoder(repo).search("존재하지않는장소이름") == []


class TestKakaoParsing:
    def test_keyword_document(self):
        hit = _parse_kakao(KEYWORD_SAMPLE, "kakao-keyword")[0]
        assert hit.name == "스타벅스 강남역중앙점"
        assert (hit.lat, hit.lon) == (37.4979, 127.0276)  # y=위도, x=경도
        assert hit.address == "서울 강남구 강남대로 390"  # 도로명이 우선
        assert hit.category == "카페"
        assert hit.distance_m == 412

    def test_address_document(self):
        hit = _parse_kakao(ADDRESS_SAMPLE, "kakao-address")[0]
        assert hit.name == "서울 중구 세종대로 110"
        assert (hit.lat, hit.lon) == (37.5665, 126.9780)
        assert hit.source == "kakao-address"

    def test_skips_documents_without_coordinates(self):
        body = {"documents": [{"place_name": "좌표없음"}, {"place_name": "x", "x": "", "y": ""}]}
        assert _parse_kakao(body, "kakao-keyword") == []

    def test_empty_and_malformed_bodies(self):
        assert _parse_kakao({}, "kakao-keyword") == []
        assert _parse_kakao({"documents": None}, "kakao-keyword") == []

    def test_extra_fields_are_ignored(self):
        """실물에는 id·phone·place_url 이 함께 온다. 모르는 키가 늘어도 읽혀야 한다."""
        hit = _parse_kakao(KEYWORD_SAMPLE, "kakao-keyword")[0]
        assert hit.name == "스타벅스 강남역중앙점"

    def test_missing_optional_fields_do_not_raise(self):
        body = {"documents": [{"place_name": "이름만", "x": "127.0", "y": "37.5"}]}
        hit = _parse_kakao(body, "kakao-keyword")[0]
        assert hit.address is None and hit.category is None and hit.distance_m is None


class TestKakaoRequest:
    def _client(self, handler):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_sends_the_rest_key_header(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen[request.url.path] = request.headers.get("Authorization", "")
            return httpx.Response(200, json=KEYWORD_SAMPLE)

        async with self._client(handler) as c:
            hits = await KakaoGeocoder("KEY123", c).search("스타벅스")
        assert set(seen.values()) == {"KakaoAK KEY123"}
        assert hits

    async def test_queries_both_keyword_and_address(self):
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(200, json={"documents": []})

        async with self._client(handler) as c:
            await KakaoGeocoder("K", c).search("세종대로 110")
        assert sorted(paths) == [
            "/v2/local/search/address.json",
            "/v2/local/search/keyword.json",
        ]

    async def test_one_endpoint_failing_does_not_kill_the_other(self):
        """부분 실패 허용. 도착정보 병렬 조회와 같은 원칙이다."""

        def handler(request: httpx.Request) -> httpx.Response:
            if "address" in request.url.path:
                return httpx.Response(500)
            return httpx.Response(200, json=KEYWORD_SAMPLE)

        async with self._client(handler) as c:
            hits = await KakaoGeocoder("K", c).search("스타벅스")
        assert len(hits) == 1

    async def test_no_key_means_no_request(self):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("키가 없으면 부르지 않아야 한다")

        async with self._client(handler) as c:
            assert await KakaoGeocoder("", c).search("스타벅스") == []

    async def test_does_not_send_radius_or_distance_sort(self):
        """목적지 검색이므로 현재 위치 기준으로 자르거나 정렬하면 안 된다.

        반경을 주면 반경 밖 목적지가 지워지고, 거리순 정렬을 주면 '내가 말한
        그 지점'이 뒤로 밀린다.
        """
        seen: list[httpx.URL] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url)
            return httpx.Response(200, json={"documents": []})

        async with self._client(handler) as c:
            await KakaoGeocoder("K", c).search("회사", near=(37.5, 127.0))
        kw = next(u for u in seen if "keyword" in u.path)
        assert kw.params["x"] == "127.0" and kw.params["y"] == "37.5"
        assert "radius" not in kw.params and "sort" not in kw.params


class Fixed(Geocoder):
    def __init__(self, hits: list[PlaceHit], boom: bool = False):
        self.hits = hits
        self.boom = boom

    async def search(self, query, limit=8, near=None):
        if self.boom:
            raise RuntimeError("공급원 장애")
        return self.hits


def hit(name, lat=37.5, lon=127.0, source="s"):
    return PlaceHit(name=name, lat=lat, lon=lon, source=source)


class TestChain:
    async def test_interleaves_so_one_source_cannot_fill_the_list(self):
        """정류장 검색이 8칸을 다 먹으면 카카오 결과가 화면에 못 올라온다."""
        stops = Fixed([hit(f"정류장{i}", lat=37.5 + i / 1000, source="stop") for i in range(8)])
        kakao = Fixed([hit(f"건물{i}", lat=37.6 + i / 1000, source="kakao") for i in range(8)])
        out = await ChainGeocoder([stops, kakao]).search("강남", limit=4)
        assert [h.source for h in out] == ["stop", "kakao", "stop", "kakao"]

    async def test_a_dead_source_does_not_break_the_search(self):
        out = await ChainGeocoder([Fixed([], boom=True), Fixed([hit("살아있음")])]).search("x")
        assert [h.name for h in out] == ["살아있음"]

    async def test_same_place_from_two_sources_appears_once(self):
        same = [hit("시청", 37.5665, 126.9780)]
        out = await ChainGeocoder([Fixed(same), Fixed(list(same))]).search("시청")
        assert len(out) == 1

    async def test_respects_limit(self):
        many = Fixed([hit(f"p{i}", lat=37.5 + i / 1000) for i in range(20)])
        assert len(await ChainGeocoder([many]).search("p", limit=3)) == 3


class TestBuild:
    async def test_without_a_key_only_stops_are_searched(self, repo):
        g = build_geocoder(repo, "", None)
        assert isinstance(g, ChainGeocoder)
        assert [type(s) for s in g.sources] == [StopGeocoder]
        assert await g.search("상계주공")  # 키가 없어도 검색은 된다

    async def test_with_a_key_kakao_is_added(self, repo):
        async with httpx.AsyncClient() as c:
            g = build_geocoder(repo, "K", c)
            assert [type(s) for s in g.sources] == [StopGeocoder, KakaoGeocoder]

    async def test_a_key_without_a_client_is_ignored(self, repo):
        """lifespan 을 타지 않는 경우. 검색이 죽는 것보다 덜 찾는 게 낫다."""
        g = build_geocoder(repo, "K", None)
        assert [type(s) for s in g.sources] == [StopGeocoder]

    async def test_a_non_ascii_key_is_ignored(self, repo):
        """.env 에 안내문의 자리표시자('발급받은키')를 그대로 넣은 실제 사례.

        HTTP 헤더는 ASCII 만 담는다. 그대로 넘기면 요청 경로에서
        UnicodeEncodeError 가 터진다. 카카오만 빼고 정류장 검색은 살린다.
        """
        async with httpx.AsyncClient() as c:
            g = build_geocoder(repo, "발급받은키", c)
            assert [type(s) for s in g.sources] == [StopGeocoder]
            assert await g.search("상계주공")


class TestUsableKey:
    def test_strips_surrounding_whitespace(self):
        assert usable_kakao_key("  abc123  ") == "abc123"

    def test_rejects_non_ascii_and_embedded_spaces(self):
        assert usable_kakao_key("발급받은키") == ""
        assert usable_kakao_key("abc 123") == ""
        assert usable_kakao_key("") == ""
