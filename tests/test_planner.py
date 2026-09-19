"""목 Provider 주입 → 실 API 없이 end-to-end. 계획서 §7.

정적 데이터는 실제 146번 픽스처를 쓴다. 좌표·seq·정류장 ID 가 전부 실물이라
'테스트를 위한 가짜 노선'이 만들어내는 착시가 없다.
"""

import pytest

from nowbus.config import Settings
from nowbus.core.planner import plan_now
from nowbus.db.repo import StaticRepo, init_schema
from nowbus.models import Arrival, Catch, Stop
from nowbus.providers.base import ArrivalProvider
from nowbus.providers.seoul import parse_route_stops

ROUTE = "100100025"  # 146번
SANGGYE = "110000399"  # seq   1  7단지영업소
GANGNAM9 = "121000091"  # seq  66  강남역9번출구


class MockProvider(ArrivalProvider):
    """정류장별 도착정보를 그대로 돌려준다. 호출된 정류장을 기록한다."""

    def __init__(self, table: dict[str, list[Arrival]], fail: set[str] | None = None):
        super().__init__(cache_ttl_sec=0)
        self.table = table
        self.fail = fail or set()
        self.asked: list[str] = []

    async def _fetch(self, stop: Stop) -> list[Arrival]:
        self.asked.append(stop.stop_id)
        if stop.stop_id in self.fail:
            raise TimeoutError("정류장 조회 실패")
        return self.table.get(stop.stop_id, [])


def arr(stop_id, eta, order=1, route=ROUTE, **kw):
    return Arrival(route_id=route, stop_id=stop_id, eta_min=eta, order=order, **kw)


@pytest.fixture
def repo(fx):
    r = StaticRepo(":memory:")
    init_schema(r.conn)
    r.upsert_route_stops(parse_route_stops(fx("getStaionByRoute")))
    yield r
    r.close()


@pytest.fixture
def coords(repo):
    def at(stop_id):
        row = repo.conn.execute(
            "SELECT lat, lon FROM stop WHERE stop_id = ?", (stop_id,)
        ).fetchone()
        return (row["lat"], row["lon"])

    return at


def offset(coord, meters_north):
    """정류장에서 북쪽으로 떨어진 지점. 도보 시간을 0 이 아니게 만든다."""
    return (coord[0] + meters_north / 111_195.0, coord[1])


async def test_end_to_end(repo, coords):
    """상계동 → 강남역. 7단계가 전부 이어지는지."""
    provider = MockProvider({SANGGYE: [arr(SANGGYE, 8, 1, headway_min=10.0)]})
    out = await plan_now(coords(SANGGYE), coords(GANGNAM9), repo, provider, Settings())

    assert out.best, "직통 조합이 나와야 한다"
    p = out.best[0]
    assert p.route.route_id == ROUTE
    assert p.board.stop_id == SANGGYE
    assert p.catch is Catch.SAFE
    # 총 시간 = max(버스 8분, 도보) + 승차 + 하차 도보
    assert p.total_min > p.ride_min
    assert p.score > 0


async def test_unreachable_bus_is_dropped(repo, coords):
    """도보 8분 거리인데 곧 도착하는 버스뿐이면 후보에서 빠진다.

    이 프로그램의 존재 이유. 기존 지도앱은 이걸 '곧 도착'이라고 그대로 보여준다.
    """
    here = offset(coords(SANGGYE), 400)  # 도보 약 7.8분
    provider = MockProvider({SANGGYE: [arr(SANGGYE, 0.2, 1)]})  # 다음 차 정보 없음
    out = await plan_now(here, coords(GANGNAM9), repo, provider, Settings())
    assert out.best == []
    assert out.sprint == [], "도보 8분 거리는 뛰어도 12초 안에 못 간다"


async def test_next_bus_is_used_when_the_first_is_missed(repo, coords):
    """1차를 놓쳐도 2차가 잡히면 후보로 남는다. 무조건 버리는 게 아니다."""
    here = offset(coords(SANGGYE), 400)
    provider = MockProvider({SANGGYE: [arr(SANGGYE, 0.2, 1), arr(SANGGYE, 15, 2)]})
    out = await plan_now(here, coords(GANGNAM9), repo, provider, Settings())
    assert out.best
    assert out.best[0].eta_min == 15
    assert out.best[0].catch is Catch.SAFE


async def test_only_queries_stops_that_have_a_direct_route(repo, coords):
    """직통이 없는 정류장은 실시간 조회조차 하지 않는다. 호출 예산 문제."""
    provider = MockProvider({SANGGYE: [arr(SANGGYE, 8, 1)]})
    await plan_now(coords(SANGGYE), coords(GANGNAM9), repo, provider, Settings())
    assert provider.asked, "최소한 승차 후보는 물어봐야 한다"
    for sid in provider.asked:
        combos = repo.find_direct_combos([sid], [GANGNAM9])
        assert combos, f"{sid} 는 직통이 없는데 조회했다"


async def test_respects_origin_stop_cap(repo, coords):
    """MAX_ORIGIN_STOPS 상한. 없으면 강남 반경 600m 에서 API 를 39번 부른다."""
    cfg = Settings(radius_origin_m=3000, max_origin_stops=2)
    provider = MockProvider({})
    await plan_now(coords(SANGGYE), coords(GANGNAM9), repo, provider, cfg)
    assert len(set(provider.asked)) <= 2


async def test_cap_is_applied_after_finding_direct_routes(repo, coords):
    """상한을 조합 탐색 **전에** 걸면 안 된다.

    가까운 정류장에 직통이 없고 먼 정류장에만 있는 상황을 만든다. 거리순으로
    먼저 자르면 후보가 0 이 되고, 조합을 찾은 뒤 자르면 정상적으로 나온다.

    실측 근거: 강남역 반경 600m 에 정류장 39곳인데 시청행 직통이 있는 곳은
    8곳뿐이고 거리순 상위 8곳과 거의 겹치지 않는다. 먼저 자르면 노선 9개 중
    1개만 남았다.
    """
    # 146번이 지나지 않는 가짜 정류장을 7단지영업소 바로 옆에 심는다.
    real = repo.get_stops([SANGGYE])[SANGGYE]
    for i in range(5):
        repo.conn.execute(
            "INSERT INTO stop(stop_id, ars_id, name, lat, lon) VALUES (?,?,?,?,?)",
            (f"FAKE{i}", f"9{i}", f"노선없는정류장{i}", real.lat + i * 1e-5, real.lon),
        )
    repo.conn.commit()

    cfg = Settings(max_origin_stops=3)
    provider = MockProvider({SANGGYE: [arr(SANGGYE, 8, 1, headway_min=10.0)]})
    out = await plan_now(coords(SANGGYE), coords(GANGNAM9), repo, provider, cfg)

    assert out.best, "가짜 정류장이 앞을 막아도 직통을 찾아야 한다"
    assert all(not sid.startswith("FAKE") for sid in provider.asked), (
        "직통 없는 정류장에는 API 를 쓰지 않는다"
    )


async def test_partial_failure_still_returns_results(repo, coords):
    """정류장 한 곳이 타임아웃 나도 나머지로 결과를 만든다.

    예외를 전파하면 정작 필요한 아침에 앱이 통째로 죽는다.
    """
    cfg = Settings(radius_origin_m=1500)
    near = repo.stops_within(*coords(SANGGYE), 1500)
    assert len(near) >= 2
    table = {s.stop_id: [arr(s.stop_id, 8, 1, headway_min=10.0)] for s in near}
    provider = MockProvider(table, fail={near[0].stop_id})

    out = await plan_now(coords(SANGGYE), coords(GANGNAM9), repo, provider, cfg)
    assert out.best, "한 곳이 실패해도 나머지로 결과가 나와야 한다"
    assert all(p.board.stop_id != near[0].stop_id for p in out.best)


async def test_no_stops_nearby(repo, coords):
    """제주도 좌표. 반경 안에 정류장이 없다."""
    provider = MockProvider({})
    out = await plan_now((33.5, 126.5), coords(GANGNAM9), repo, provider, Settings())
    assert not out
    assert provider.asked == []


async def test_no_direct_route(repo, coords):
    """출발·도착 모두 정류장은 있는데 잇는 직통 노선이 없다."""
    provider = MockProvider({})
    # 같은 방향 뒤쪽에서 앞쪽으로 = seq 감소 = 직통 없음
    out = await plan_now(coords(GANGNAM9), coords(SANGGYE), repo, provider, Settings())
    assert not out


async def test_top_n_and_diversity_applied(repo, coords):
    """랭킹 단계가 실제로 걸리는지. 반경을 넓혀 후보를 많이 만든다."""
    cfg = Settings(radius_origin_m=2000, radius_dest_m=2000, max_origin_stops=8)
    near = repo.stops_within(*coords(SANGGYE), 2000)
    table = {s.stop_id: [arr(s.stop_id, 6, 1, headway_min=10.0)] for s in near}
    out = await plan_now(coords(SANGGYE), coords(GANGNAM9), repo, provider_of(table), cfg)
    assert len(out.best) <= cfg.top_n
    assert [p.score for p in out.best] == sorted(p.score for p in out.best)


def provider_of(table):
    return MockProvider(table)


class TestSprint:
    """'1~2분 뒤 도착하는 버스가 목록에 안 나온다'는 실사용 불만에 대한 회귀 테스트.

    도보 2.9분 / 뛰어서 1.4분 지점에서 2분 뒤 도착하는 버스 - 걷기로는 놓치지만
    뛰면 잡는다. 예전에는 이 차를 건너뛰고 다음 차만 보여줬다.
    """

    HERE = 150  # 정류장에서 북쪽 150m

    async def test_imminent_bus_is_offered_as_a_sprint(self, repo, coords):
        here = offset(coords(SANGGYE), self.HERE)
        provider = MockProvider({SANGGYE: [arr(SANGGYE, 2, 1), arr(SANGGYE, 12, 2)]})
        out = await plan_now(here, coords(GANGNAM9), repo, provider, Settings())

        assert out.sprint, "뛰면 잡을 수 있는 버스가 나와야 한다"
        s = out.sprint[0]
        assert s.catch is Catch.RUN
        assert s.eta_min == 2
        assert s.run_to_board_min is not None
        assert s.run_to_board_min < s.walk_to_board_min
        assert s.margin_min == pytest.approx(2 - s.run_to_board_min)

    async def test_walking_candidate_survives_alongside_the_sprint(self, repo, coords):
        """둘 다 나와야 한다. '뛰면 2분 뒤 차, 안 뛰면 12분 뒤 차' 가 동시에 사실이다."""
        here = offset(coords(SANGGYE), self.HERE)
        provider = MockProvider({SANGGYE: [arr(SANGGYE, 2, 1), arr(SANGGYE, 12, 2)]})
        out = await plan_now(here, coords(GANGNAM9), repo, provider, Settings())

        assert [p.eta_min for p in out.best if p.board.stop_id == SANGGYE] == [12]
        assert [p.eta_min for p in out.sprint] == [2]

    async def test_sprint_total_uses_run_time_not_walk_time(self, repo, coords):
        """뛰는 후보의 총 소요시간은 뛰는 시간으로 재야 한다. 도보로 재면 과대평가된다."""
        here = offset(coords(SANGGYE), self.HERE)
        provider = MockProvider({SANGGYE: [arr(SANGGYE, 2, 1), arr(SANGGYE, 12, 2)]})
        out = await plan_now(here, coords(GANGNAM9), repo, provider, Settings())
        s = out.sprint[0]
        assert s.total_min == pytest.approx(
            max(s.eta_min, s.run_to_board_min) + s.ride_min + s.walk_from_alight_min
        )
        assert s.total_min < out.best[0].total_min

    async def test_far_stops_are_never_sprint_candidates(self, repo, coords):
        """MAX_RUN_MIN 상한. 3분 넘게 전력질주하라는 추천은 하지 않는다."""
        here = offset(coords(SANGGYE), 400)  # 도보 약 7.8분, 뛰어도 3.7분
        provider = MockProvider({SANGGYE: [arr(SANGGYE, 4, 1), arr(SANGGYE, 20, 2)]})
        out = await plan_now(here, coords(GANGNAM9), repo, provider, Settings())
        assert out.sprint == []

    async def test_no_sprint_when_walking_is_enough(self, repo, coords):
        """정류장 바로 앞이면 뛸 이유가 없다. 뛰는 목록은 비어야 한다."""
        provider = MockProvider({SANGGYE: [arr(SANGGYE, 5, 1, headway_min=10.0)]})
        out = await plan_now(coords(SANGGYE), coords(GANGNAM9), repo, provider, Settings())
        assert out.best
        assert out.sprint == []

    async def test_sprint_is_dropped_when_it_saves_nothing(self):
        """뛰어도 더 빠르지 않으면 보여주지 않는다. rank 가 아니라 이 규칙이 거른다."""
        from nowbus.core.planner import _rank_sprints

        def fake(total, catch=Catch.RUN, stop_id="A", route_id="R"):
            from nowbus.models import Plan, Route, Stop

            st = Stop(stop_id, stop_id, stop_id, 37.5, 127.0)
            return Plan(
                board=st,
                alight=Stop("D", "D", "D", 37.6, 127.0),
                route=Route(route_id, route_id),
                walk_to_board_min=3.0,
                walk_from_alight_min=2.0,
                eta_min=2.0,
                ride_min=total - 5.0,
                total_min=total,
                margin_min=0.6,
                catch=catch,
                score=0.0,
                run_to_board_min=1.4,
            )

        cfg = Settings()
        slower = fake(30.0)
        faster = fake(20.0)
        best = [fake(25.0, catch=Catch.SAFE, stop_id="B", route_id="R2")]
        assert _rank_sprints([slower], best, cfg) == []
        assert len(_rank_sprints([faster], best, cfg)) == 1
        # 편한 후보가 아예 없으면 뛰는 게 유일한 수단이므로 남긴다.
        assert len(_rank_sprints([slower], [], cfg)) == 1
