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
