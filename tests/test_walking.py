"""walking.py 는 순수 함수라 가장 먼저 테스트로 고정한다. 계획서 §7."""

import pytest

from nowbus.core.walking import haversine_m, walk_minutes

SEOUL_CITY_HALL = (37.5665, 126.9780)
BUSAN_CITY_HALL = (35.1796, 129.0756)


def test_same_point_is_zero():
    assert haversine_m(*SEOUL_CITY_HALL, *SEOUL_CITY_HALL) == pytest.approx(0.0, abs=1e-6)


def test_one_degree_of_latitude():
    """위도 1도는 어디서나 약 111.19km. 공식이 맞는지 보는 가장 확실한 기준점."""
    d = haversine_m(37.0, 127.0, 38.0, 127.0)
    assert d == pytest.approx(111_195, rel=0.001)


def test_known_long_distance():
    """서울시청 ~ 부산시청 직선거리는 약 325km로 알려져 있다."""
    d = haversine_m(*SEOUL_CITY_HALL, *BUSAN_CITY_HALL)
    assert d == pytest.approx(325_000, rel=0.02)


def test_symmetry():
    a = haversine_m(*SEOUL_CITY_HALL, *BUSAN_CITY_HALL)
    b = haversine_m(*BUSAN_CITY_HALL, *SEOUL_CITY_HALL)
    assert a == pytest.approx(b)


def test_longitude_degree_shrinks_with_latitude():
    """경도 1도의 실거리는 위도가 높을수록 짧아진다. cos 항이 살아있는지 확인."""
    at_equator = haversine_m(0.0, 127.0, 0.0, 128.0)
    at_seoul = haversine_m(37.5, 127.0, 37.5, 128.0)
    assert at_seoul < at_equator
    assert at_seoul == pytest.approx(at_equator * 0.7934, rel=0.01)  # cos(37.5)


def test_walk_minutes_applies_detour_and_speed():
    """670m 직선 = 우회 1.3배 → 871m, 67m/min → 13분."""
    d = haversine_m(37.5, 127.0, 37.5, 127.0)
    assert d == 0
    # 위도로 정확히 670m 떨어진 점을 만든다.
    dlat = 670 / 111_195
    got = walk_minutes(37.5, 127.0, 37.5 + dlat, 127.0)
    assert got == pytest.approx(670 * 1.30 / 67.0, rel=0.001)  # = 13.0분


def test_calib_scales_linearly():
    """CALIB 은 F-11 피드백으로 갱신되는 개인 보정계수. 선형이어야 학습이 단순해진다."""
    args = (37.5, 127.0, 37.51, 127.01)
    base = walk_minutes(*args)
    assert walk_minutes(*args, calib=2.0) == pytest.approx(base * 2)


def test_detour_never_shortens():
    """우회 보정은 1.0 이상이어야 한다. 직선보다 짧은 도보는 없다."""
    args = (37.5, 127.0, 37.51, 127.01)
    straight = haversine_m(*args) / 67.0
    assert walk_minutes(*args) >= straight
