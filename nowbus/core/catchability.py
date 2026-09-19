"""탑승 가능성 판정. 설계서 §6.2.

이 파일이 프로그램의 존재 이유다. 도보 6분 거리 정류장에 3분 뒤 오는 버스를
'3분 후 도착'이라고 보여주는 것이 기존 지도앱의 문제이고, 여기서 그걸 걸러낸다.
"""

from __future__ import annotations

from dataclasses import replace

from nowbus.models import Arrival, Catch


def judge(margin_min: float, m_safe: float = 2.0, run_margin_min: float | None = None) -> Catch:
    """여유시간 등급. 경계는 닫힌 쪽이 안전한 쪽이다.

    margin == m_safe 는 SAFE, margin == 0 은 TIGHT.

    run_margin_min 을 주면 MISS 로 떨어지기 전에 RUN 을 한 단계 더 본다.
    걸어선 늦지만(margin < 0) 뛰면 도착 전에 닿는(run_margin >= 0) 경우다.
    주지 않으면 예전과 똑같이 MISS 로 간다 - 뛰는 시간을 모르는 호출부는
    RUN 을 만들어낼 수 없어야 한다.
    """
    if margin_min >= m_safe:
        return Catch.SAFE
    if margin_min >= 0:
        return Catch.TIGHT
    if run_margin_min is not None and run_margin_min >= 0:
        return Catch.RUN
    return Catch.MISS


def pick_boardable(
    arrivals: list[Arrival],
    walk_min: float,
    m_safe: float = 2.0,
    headway_min: float | None = None,
) -> tuple[Arrival, Catch] | None:
    """**걸어서** 탈 수 있는 최초 차량을 고른다.

    뛰는 경우는 여기서 보지 않는다. RUN 을 여기 섞으면 1분 뒤 도착하는 차가
    SAFE 한 다음 차를 가려버린다 - '뛰면 잡힌다'와 '여유 있게 잡힌다'는 서로를
    대체하지 못하므로 둘 다 살아 있어야 한다. 뛰는 쪽은 pick_sprint() 가 본다.

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


def pick_sprint(
    arrivals: list[Arrival],
    walk_min: float,
    run_min: float,
) -> tuple[Arrival, Catch] | None:
    """걸어선 놓치지만 뛰면 잡히는 최초 차량. 없으면 None.

    실사용 불만이 여기서 나왔다. 도보 3분 정류장에 1분 뒤 도착하는 버스는
    걷기 기준으로 MISS 라 pick_boardable() 이 건너뛰고 다음 차를 고른다. 그런데
    사용자는 뛰어서 충분히 잡을 수 있었고, 목록에 없으니 다른 앱을 다시 봤다.

    걸어서 잡히는 차를 만나면 즉시 멈춘다. 그 차가 있으면 뛸 이유가 없고,
    그보다 뒤차를 뛰어서 잡는다는 건 말이 안 된다.

    배차간격 추정(3차)은 하지 않는다. 존재를 모르는 차를 위해 뛰라고 할 수는 없다.
    """
    if not arrivals or run_min <= 0:
        return None
    for a in sorted(arrivals, key=lambda x: x.order):
        if a.eta_min - walk_min >= 0:
            return None
        if a.eta_min - run_min >= 0:
            return a, Catch.RUN
    return None
