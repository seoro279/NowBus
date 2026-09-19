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


def run_minutes(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    *,
    speed_m_per_min: float = 140.0,
    detour: float = 1.30,
    calib: float = 1.0,
) -> float:
    """뛰어서 갈 때의 소요 분. 우회 보정과 개인 보정계수는 도보와 같이 쓴다.

    도보와 다른 건 속도뿐이므로 walk_minutes 를 그대로 재사용한다. 별 함수로
    두는 이유는 호출부에서 '이건 뛰는 시간'임이 드러나야 하기 때문이다.

    **신호등 대기는 모델에 없다.** 실제로는 횡단보도 하나에서 다 잃을 수 있다.
    그래서 이 값으로 잡은 후보는 본 추천 목록에 섞지 않고 따로 낸다 - 뛸지 말지는
    사용자가 창밖을 보고 정한다.
    """
    return walk_minutes(
        lat1, lon1, lat2, lon2, speed_m_per_min=speed_m_per_min, detour=detour, calib=calib
    )
