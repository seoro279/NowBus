"""직통 조합 탐색. 설계서 §9.1 질의의 실체를 실제 146번 데이터로 검증한다.

계획서 §7 이 요구한 '역방향이 걸러지는지' 확인이 이 파일의 존재 이유다.
"""

import pytest

from nowbus.db.repo import StaticRepo, init_schema
from nowbus.providers.seoul import parse_route_stops

# 146번 실물에서 뽑은 좌표. seq 와 direction 이 핵심이다.
CHILDANJI = "110000399"  # seq   1  dir=강남역        7단지영업소 (상계동)
GANGNAM12 = "122000181"  # seq  65  dir=강남역        강남역12번출구
GANGNAM9 = "121000091"  # seq  66  dir=강남역        강남역9번출구
GANGNAM1 = "122000184"  # seq  70  dir=상계주공7단지   강남역1번출구.역삼세무서


@pytest.fixture
def repo(fx):
    r = StaticRepo(":memory:")
    init_schema(r.conn)
    r.upsert_route_stops(parse_route_stops(fx("getStaionByRoute")))
    yield r
    r.close()


def test_loads_full_route(repo):
    n = repo.conn.execute("SELECT COUNT(*) FROM route_stop").fetchone()[0]
    assert n == 135


def test_forward_combo_is_found(repo):
    """상계동 → 강남역. 이 노선의 정상적인 이용 방향이다."""
    combos = repo.find_direct_combos([CHILDANJI], [GANGNAM9])
    assert len(combos) == 1
    assert combos[0].route_id == "100100025"
    assert combos[0].n_stops == 65


def test_reverse_direction_is_rejected(repo):
    """강남역 → 상계동을 '강남역행' 승차로 잡으면 안 된다. seq 가 감소한다."""
    assert repo.find_direct_combos([GANGNAM9], [CHILDANJI]) == []


def test_crossing_the_turnaround_is_rejected(repo):
    """이 테스트가 이 파일의 핵심이다.

    강남역9번출구(seq 66)와 강남역1번출구(seq 70)는 seq 만 보면 66 < 70 이라
    유효한 조합처럼 보인다. 하지만 그 사이 seq 69 에서 direction 이
    '강남역' → '상계주공7단지' 로 바뀐다. 즉 회차 지점을 넘는 조합이고,
    실제로 타려면 종점까지 갔다가 돌아와야 한다.

    direction 조인이 빠지면 이 조합이 살아남아 사용자를 반대 방향 버스에
    태운다. 계획서 §10-1 이 '치명적 버그'라고 부른 바로 그것.
    """
    assert repo.find_direct_combos([GANGNAM9], [GANGNAM1]) == []


def test_same_direction_short_hop_survives(repo):
    """회차를 넘지 않는 짧은 구간은 정상적으로 잡혀야 한다.
    위 테스트가 direction 조인을 너무 세게 걸어 멀쩡한 조합까지 죽이지 않았는지 본다."""
    combos = repo.find_direct_combos([GANGNAM12], [GANGNAM9])
    assert len(combos) == 1
    assert combos[0].n_stops == 1


def test_multiple_origins_and_dests(repo):
    """출발·도착 양쪽 모두 복수 후보를 전개하는 것이 이 프로그램의 전제다.

    조합 4가지 중 살아남아야 하는 건 하나뿐이다:
      7단지 -> 강남역12   seq  1 -> 65, 같은 방향          O
      7단지 -> 강남역1    seq  1 -> 70, 회차 넘김          X
      강남역9 -> 강남역12  seq 66 -> 65, seq 감소           X
      강남역9 -> 강남역1   seq 66 -> 70, 회차 넘김          X
    """
    combos = repo.find_direct_combos([CHILDANJI, GANGNAM9], [GANGNAM12, GANGNAM1])
    pairs = {(c.board_stop_id, c.alight_stop_id) for c in combos}
    assert pairs == {(CHILDANJI, GANGNAM12)}


def test_empty_input_is_safe(repo):
    assert repo.find_direct_combos([], [GANGNAM9]) == []
    assert repo.find_direct_combos([CHILDANJI], []) == []


def test_stops_within_radius(repo):
    """강남역9번출구 좌표 기준 반경 조회. bbox → haversine 2단 필터."""
    row = repo.conn.execute("SELECT lat, lon FROM stop WHERE stop_id = ?", (GANGNAM9,)).fetchone()
    near = repo.stops_within(row["lat"], row["lon"], 400)
    ids = [s.stop_id for s in near]
    assert ids[0] == GANGNAM9  # 자기 자신이 거리 0 이므로 맨 앞
    assert GANGNAM12 in ids
    assert CHILDANJI not in ids  # 상계동은 강남역에서 반경 400m 밖이다


def test_stops_within_is_distance_sorted(repo):
    row = repo.conn.execute("SELECT lat, lon FROM stop WHERE stop_id = ?", (GANGNAM9,)).fetchone()
    from nowbus.core.walking import haversine_m

    near = repo.stops_within(row["lat"], row["lon"], 600)
    dists = [haversine_m(row["lat"], row["lon"], s.lat, s.lon) for s in near]
    assert dists == sorted(dists)
