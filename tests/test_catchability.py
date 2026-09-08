"""경계값이 핵심이다. 계획서 §7 이 margin = -0.1 / 0 / 1.9 / 2.0 / 2.1 을 요구했다."""

import pytest

from nowbus.core.catchability import judge, pick_boardable
from nowbus.models import Arrival, Catch


def arr(eta, order=1, **kw):
    return Arrival(route_id="R", stop_id="S", eta_min=eta, order=order, **kw)


@pytest.mark.parametrize(
    "margin,expected",
    [
        (-5.0, Catch.MISS),
        (-0.1, Catch.MISS),
        (0.0, Catch.TIGHT),  # 딱 맞으면 뛰어서라도 탄다
        (1.9, Catch.TIGHT),
        (2.0, Catch.SAFE),  # 경계는 안전한 쪽으로 닫는다
        (2.1, Catch.SAFE),
    ],
)
def test_judge_boundaries(margin, expected):
    assert judge(margin, m_safe=2.0) == expected


def test_picks_first_catchable():
    """1차가 여유 있으면 그걸 쓴다."""
    a, catch = pick_boardable([arr(7, 1), arr(15, 2)], walk_min=4.0)
    assert a.order == 1 and catch is Catch.SAFE


def test_skips_missed_first_bus():
    """도보 6분인데 3분 뒤 오는 버스는 못 탄다. 이게 이 프로그램의 존재 이유."""
    a, catch = pick_boardable([arr(3, 1), arr(12, 2)], walk_min=6.0)
    assert a.order == 2
    assert catch is Catch.SAFE


def test_tight_is_kept_not_dropped():
    """여유 1분은 후보로 남긴다. 뛰면 탈 수 있으므로 판단은 사용자 몫."""
    a, catch = pick_boardable([arr(7, 1)], walk_min=6.0)
    assert a.order == 1 and catch is Catch.TIGHT


def test_estimates_third_bus_from_headway():
    """1·2차 모두 MISS 면 배차간격으로 3차를 추정해 참고용으로 남긴다."""
    res = pick_boardable([arr(2, 1), arr(5, 2)], walk_min=10.0, headway_min=8.0)
    assert res is not None
    a, catch = res
    assert a.is_estimated is True
    assert a.order == 3
    assert a.eta_min == pytest.approx(13.0)  # 2차 5분 + 배차 8분
    assert catch is Catch.SAFE


def test_estimated_bus_drops_stale_congestion():
    """추정 차량에 앞차의 혼잡도를 물려주면 없는 정보를 지어내는 셈이다."""
    res = pick_boardable(
        [arr(2, 1, congestion=3), arr(5, 2, congestion=4)],
        walk_min=10.0,
        headway_min=8.0,
    )
    assert res[0].congestion is None


def test_no_estimate_after_last_bus():
    """막차 뒤에 다음 차는 없다. 심야에 잘못된 추천이 나가면 안 된다."""
    assert pick_boardable([arr(2, 1, is_last=True)], walk_min=10.0, headway_min=8.0) is None


def test_no_headway_means_no_candidate():
    assert pick_boardable([arr(2, 1)], walk_min=10.0, headway_min=None) is None


def test_empty_arrivals():
    assert pick_boardable([], walk_min=5.0) is None


def test_m_safe_is_configurable():
    """여유 있게 다니고 싶으면 M_safe 를 올린다. 같은 상황이 SAFE 에서 TIGHT 로."""
    a2 = pick_boardable([arr(7, 1)], walk_min=4.0, m_safe=2.0)
    a4 = pick_boardable([arr(7, 1)], walk_min=4.0, m_safe=4.0)
    assert a2[1] is Catch.SAFE
    assert a4[1] is Catch.TIGHT
