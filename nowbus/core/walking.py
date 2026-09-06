"""도보 시간 추정. 설계서 §6.5.

순수 함수만 둔다. 네트워크도 DB도 없다.
v2에서 카카오/네이버 도보 경로 API로 교체할 수 있도록 walk_minutes() 시그니처를
경계로 삼는다.
"""

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 간 대권거리(m)."""
    p1, p2 = radians(lat1), radians(lat2)
    dp = p2 - p1
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(a))


def walk_minutes(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    *,
    speed_m_per_min: float = 67.0,
    detour: float = 1.30,
    calib: float = 1.0,
) -> float:
    """직선거리에 우회 보정을 곱해 도보 소요 분을 추정한다.

    detour: 도로 우회 보정(기본 1.30). 직선거리는 항상 실제보다 짧다.
    calib:  개인 보정계수. 초기 1.0, F-11 실측 피드백으로 갱신.
    """
    d_effective = haversine_m(lat1, lon1, lat2, lon2) * detour
    return d_effective / speed_m_per_min * calib
