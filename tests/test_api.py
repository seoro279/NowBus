"""ASGITransport 로 서버 기동 없이 엔드포인트 검증. 계획서 §7."""

import httpx
import pytest

from nowbus.config import Settings
from nowbus.db.repo import StaticRepo, init_schema
from nowbus.models import Arrival, Stop
from nowbus.providers.base import ArrivalProvider
from nowbus.providers.seoul import parse_route_stops
from nowbus.web.app import create_app

TOKEN = "test-token"
SANGGYE = "110000399"
GANGNAM9 = "121000091"


class StubProvider(ArrivalProvider):
    def __init__(self):
        super().__init__(0)

    async def _fetch(self, stop: Stop) -> list[Arrival]:
        return [
            Arrival("100100025", stop.stop_id, 9.0, 1, congestion=2, headway_min=8.0),
            Arrival("100100025", stop.stop_id, 17.0, 2, headway_min=8.0),
        ]


@pytest.fixture
def app(tmp_path, fx):
    db = tmp_path / "t.db"
    repo = StaticRepo(str(db))
    init_schema(repo.conn)
    repo.upsert_route_stops(parse_route_stops(fx("getStaionByRoute")))
    row = repo.conn.execute("SELECT lat, lon FROM stop WHERE stop_id = ?", (SANGGYE,)).fetchone()
    repo.save_place("집", row["lat"] + 0.003, row["lon"])
    row2 = repo.conn.execute("SELECT lat, lon FROM stop WHERE stop_id = ?", (GANGNAM9,)).fetchone()
    repo.save_place("회사", row2["lat"], row2["lon"])
    repo.close()

    application = create_app(Settings(db_path=str(db), api_token=TOKEN, seoul_api_key="x"))
    application.state.provider = StubProvider()  # lifespan 을 타지 않으므로 직접 넣는다
    return application


def _client(app, **kw) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", **kw)


@pytest.fixture
async def client(app):
    """토큰을 기본 헤더로 달고 있는 클라이언트."""
    async with _client(app, headers={"X-Token": TOKEN}) as c:
        yield c


@pytest.fixture
async def anon(app):
    """토큰 없는 클라이언트. httpx 는 요청의 headers={} 로 기본 헤더를 지우지
    못하고 병합만 하므로, '헤더 없음'을 시험하려면 별도 클라이언트가 필요하다."""
    async with _client(app) as c:
        yield c


class TestAuth:
    async def test_rejects_missing_token(self, anon):
        r = await anon.get("/api/plan", params={"from": "집", "to": "회사"})
        assert r.status_code == 401

    async def test_rejects_wrong_token(self, anon):
        r = await anon.get(
            "/api/plan", params={"from": "집", "to": "회사"}, headers={"X-Token": "nope"}
        )
        assert r.status_code == 401

    async def test_accepts_query_token(self, anon):
        """curl 로 찔러볼 때 편하도록 쿼리스트링도 받는다."""
        r = await anon.get("/api/plan", params={"from": "집", "to": "회사", "token": TOKEN})
        assert r.status_code == 200

    async def test_places_also_guarded(self, anon):
        assert (await anon.get("/api/places")).status_code == 401


class TestPlan:
    async def test_by_place_names(self, client):
        r = await client.get("/api/plan", params={"from": "집", "to": "회사"})
        assert r.status_code == 200
        body = r.json()
        assert body["origin_label"] == "집"
        assert body["dest_label"] == "회사"
        assert body["items"]
        assert body["generated_at"]

    async def test_by_gps_coords(self, client):
        r = await client.get("/api/plan", params={"lat": 37.6632, "lon": 127.0637, "to": "회사"})
        assert r.status_code == 200
        assert r.json()["origin_label"] == "현재 위치"

    async def test_all_time_fields_are_integers(self, client):
        """프론트가 반올림 로직을 갖지 않게 한다."""
        body = (await client.get("/api/plan", params={"from": "집", "to": "회사"})).json()
        item = body["items"][0]
        for k in ("walk_to_board_min", "eta_min", "ride_min", "total_min", "margin_min"):
            assert isinstance(item[k], int), f"{k} 가 정수가 아니다"
        assert len(item["arrive_at"]) == 5  # "09:14"

    async def test_at_most_five_items(self, client):
        body = (await client.get("/api/plan", params={"from": "집", "to": "회사"})).json()
        assert len(body["items"]) <= 5
        assert [i["rank"] for i in body["items"]] == list(range(1, len(body["items"]) + 1))

    async def test_unknown_place_is_404(self, client):
        r = await client.get("/api/plan", params={"from": "집", "to": "없는곳"})
        assert r.status_code == 404
        assert "없는곳" in r.json()["detail"]

    async def test_missing_destination_is_422(self, client):
        r = await client.get("/api/plan", params={"from": "집"})
        assert r.status_code == 422

    async def test_no_result_carries_warning(self, client):
        """제주도. 후보가 없어도 200 이되 경고를 담는다."""
        body = (
            await client.get("/api/plan", params={"lat": 33.5, "lon": 126.5, "to": "회사"})
        ).json()
        assert body["items"] == []
        assert "직통 버스가 없어요" in body["warning"]


class TestPlaces:
    async def test_list(self, client):
        r = await client.get("/api/places")
        assert {p["name"] for p in r.json()} == {"집", "회사"}

    async def test_create_then_read_back(self, client):
        r = await client.post("/api/places", json={"name": "헬스장", "lat": 37.5, "lon": 127.0})
        assert r.status_code == 201
        names = {p["name"] for p in (await client.get("/api/places")).json()}
        assert "헬스장" in names

    async def test_rejects_bad_coords(self, client):
        r = await client.post("/api/places", json={"name": "x", "lat": 999, "lon": 0})
        assert r.status_code == 422


class TestFeedback:
    async def test_records_trip_log(self, client, tmp_path):
        r = await client.post(
            "/api/feedback",
            json={"stop_id": SANGGYE, "predicted_walk_min": 5.0, "actual_walk_min": 7.5},
        )
        assert r.status_code == 204
        repo = StaticRepo(str(tmp_path / "t.db"))
        n = repo.conn.execute("SELECT COUNT(*) FROM trip_log").fetchone()[0]
        repo.close()
        assert n == 1


class TestGeocode:
    """좌표 대신 이름으로 장소를 찾는다 [F-19]. 키 없이도 정류장 검색은 된다."""

    async def test_finds_places_by_name(self, client):
        r = await client.get("/api/geocode", params={"q": "상계주공"})
        assert r.status_code == 200
        hits = r.json()
        assert hits
        assert all("상계주공" in h["name"] for h in hits)
        assert all(h["source"] == "stop" for h in hits)

    async def test_returns_coordinates_ready_to_save(self, client):
        """검색 결과를 그대로 /api/places 로 넘길 수 있어야 한다."""
        hit = (await client.get("/api/geocode", params={"q": "상계주공7단지"})).json()[0]
        r = await client.post(
            "/api/places", json={"name": "테스트장소", "lat": hit["lat"], "lon": hit["lon"]}
        )
        assert r.status_code == 201

    async def test_distance_is_filled_when_location_is_given(self, client):
        params = {"q": "상계주공7단지", "lat": 37.5, "lon": 127.0}
        hits = (await client.get("/api/geocode", params=params)).json()
        assert hits[0]["distance_m"] is not None
        assert isinstance(hits[0]["distance_m"], int)

    async def test_no_match_is_an_empty_list(self, client):
        r = await client.get("/api/geocode", params={"q": "존재하지않는장소이름"})
        assert r.status_code == 200 and r.json() == []

    async def test_empty_query_is_422(self, client):
        assert (await client.get("/api/geocode", params={"q": ""})).status_code == 422

    async def test_guarded_by_token(self, anon):
        assert (await anon.get("/api/geocode", params={"q": "시청"})).status_code == 401


class TestSprintResponse:
    """걸어선 놓치지만 뛰면 잡히는 버스가 응답에 따로 실린다."""

    async def test_sprint_field_always_exists(self, client):
        body = (await client.get("/api/plan", params={"from": "집", "to": "회사"})).json()
        assert "sprint" in body and isinstance(body["sprint"], list)

    async def test_imminent_bus_appears_in_sprint(self, app, client, tmp_path):
        """정류장에서 약 145m(도보 2.8분 / 뛰어서 1.3분) 지점에서 2분 뒤 오는 버스."""

        class Imminent(ArrivalProvider):
            def __init__(self):
                super().__init__(0)

            async def _fetch(self, stop: Stop) -> list[Arrival]:
                # 한 정류장에만 도착정보를 준다. 다른 정류장까지 2분 뒤 차를 깔면
                # 노선이 하나뿐인 픽스처에서 '더 멀리 걸어가 타는 게 총 시간은 짧은'
                # 조합이 생겨서 이 테스트가 보려는 게 가려진다.
                if stop.stop_id != SANGGYE:
                    return []
                return [
                    Arrival("100100025", stop.stop_id, 2.0, 1),
                    Arrival("100100025", stop.stop_id, 12.0, 2),
                ]

        app.state.provider = Imminent()
        repo = StaticRepo(str(tmp_path / "t.db"))
        row = repo.conn.execute(
            "SELECT lat, lon FROM stop WHERE stop_id = ?", (SANGGYE,)
        ).fetchone()
        repo.close()

        body = (
            await client.get(
                "/api/plan",
                params={"lat": row["lat"] + 0.0013, "lon": row["lon"], "to": "회사"},
            )
        ).json()

        assert body["sprint"], "뛰면 잡을 수 있는 버스가 나와야 한다"
        s = body["sprint"][0]
        assert s["catch"] == "RUN"
        assert s["eta_min"] == 2
        assert isinstance(s["run_to_board_min"], int)
        assert s["run_to_board_min"] < s["walk_to_board_min"]
        # 본 목록은 여유 있는 뒤차를 그대로 들고 있어야 한다
        assert all(i["catch"] != "RUN" for i in body["items"])
        assert 12 in [i["eta_min"] for i in body["items"]]
