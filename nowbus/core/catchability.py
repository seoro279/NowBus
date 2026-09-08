"""탑승 가능성 판정. 설계서 §6.2.

이 파일이 프로그램의 존재 이유다. 도보 6분 거리 정류장에 3분 뒤 오는 버스를
'3분 후 도착'이라고 보여주는 것이 기존 지도앱의 문제이고, 여기서 그걸 걸러낸다.
"""

from __future__ import annotations

from dataclasses import replace

from nowbus.models import Arrival, Catch


def judge(margin_min: float, m_safe: float = 2.0) -> Catch:
    """여유시간 등급. 경계는 닫힌 쪽이 안전한 쪽이다.

    margin == m_safe 는 SAFE, margin == 0 은 TIGHT.
    """
    if margin_min >= m_safe:
        return Catch.SAFE
    if margin_min >= 0:
        return Catch.TIGHT
    return Catch.MISS


def pick_boardable(
    arrivals: list[Arrival],
    walk_min: float,
    m_safe: float = 2.0,
    headway_min: float | None = None,
) -> tuple[Arrival, Catch] | None:
    """탈 수 있는 최초 차량을 고른다.

    1·2차를 순서대로 보고 MISS 가 아닌 첫 차량을 쓴다. 둘 다 MISS 면 배차간격으로
    3차 도착을 추정해 참고용 후보로 남긴다(is_estimated=True). 추정조차 못 하면
    None - 이 조합은 후보에서 빠진다.
    """
    if not arrivals:
        return None

    ordered = sorted(arrivals, key=lambda a: a.order)
    for a in ordered:
        catch = judge(a.eta_min - walk_min, m_safe)
        if catch is not Catch.MISS:
            return a, catch

    if not headway_min or headway_min <= 0:
        return None

    # 마지막 차량 + 배차간격 = 3차 추정. 막차였다면 다음 차는 없다.
    last = ordered[-1]
    if last.is_last:
        return None
    estimated = replace(
        last,
        eta_min=last.eta_min + headway_min,
        order=last.order + 1,
        is_estimated=True,
        congestion=None,
    )
    return estimated, judge(estimated.eta_min - walk_min, m_safe)
