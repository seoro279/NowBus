"""점수화 / 정렬 / 다양성 보정. 설계서 §6.4, §6.4.1."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace

from nowbus.models import Catch, Plan


@dataclass(frozen=True, slots=True)
class Weights:
    tight: float = 3.0  # '뛰어야 함' 벌점 (분 환산)
    walk: float = 0.2  # 도보 기피 성향
    transfer: float = 0.0  # MVP 는 무환승이라 항상 0


DEFAULT_WEIGHTS = Weights()


def score_plan(plan: Plan, w: Weights) -> float:
    return (
        plan.total_min
        + (w.tight if plan.catch is Catch.TIGHT else 0.0)
        + w.walk * (plan.walk_to_board_min + plan.walk_from_alight_min)
    )


def _dedupe(plans: list[Plan]) -> list[Plan]:
    """같은 (정류장, 노선)은 하나만. 2차 차량은 1차가 MISS 일 때만 올라온다."""
    best: dict[tuple[str, str], Plan] = {}
    for p in plans:
        key = (p.board.stop_id, p.route.route_id)
        if key not in best or p.score < best[key].score:
            best[key] = p
    return list(best.values())


def rank(
    plans: list[Plan],
    w: Weights = DEFAULT_WEIGHTS,
    top_n: int = 5,
    max_per_stop: int = 2,
    min_distinct_stops: int = 2,
    dominance_min: float = 5.0,
) -> list[Plan]:
    """설계서 §6.4.1 다양성 보정.

    출력을 5개로 늘리면 정렬만 했을 때 5개가 전부 같은 정류장으로 채워질 수 있다.
    그러면 '그 정류장까지 못 가는 상황'의 대안이 화면에 하나도 없어서 후보를
    늘린 의미가 사라진다.

    다만 계획서 §10-4 의 경고가 있다. max_per_stop 은 동점에 가까운 후보들
    사이의 타이브레이커여야지, 압도적으로 좋은 후보를 밀어내면 안 된다. 그래서
    상한에 걸린 후보라도 다른 정류장의 최선 대안보다 dominance_min 분 이상
    좋으면 그대로 채택한다.

    이 예외는 개수를 제한하지 않는다. 한 정류장의 후보 넷이 전부 다른 정류장보다
    5분 이상 빠르다면 넷 다 올라간다 - 그게 사실이니까. 화면에 대안이 하나도
    없어지는 것은 max_per_stop 이 아니라 min_distinct_stops 가 막는다. 상한은
    '비슷할 때 섞어라'는 규칙이고, 최소 정류장 수가 '항상 대안은 남겨라'는
    규칙이다. 둘을 헷갈리면 더 빠른 버스를 숨기게 된다.
    """
    if not plans:
        return []

    scored = [replace(p, score=score_plan(p, w)) for p in plans]
    ordered = sorted(_dedupe(scored), key=lambda p: p.score)

    chosen: list[Plan] = []
    deferred: list[Plan] = []
    per_stop: Counter[str] = Counter()

    for i, p in enumerate(ordered):
        if per_stop[p.board.stop_id] < max_per_stop:
            chosen.append(p)
            per_stop[p.board.stop_id] += 1
            continue
        # 상한 초과. 이 후보를 밀어내고 올릴 대안이 얼마나 나쁜지 본다.
        alt = next((q for q in ordered[i + 1 :] if q.board.stop_id != p.board.stop_id), None)
        if alt is None or alt.score - p.score > dominance_min:
            chosen.append(p)
            per_stop[p.board.stop_id] += 1
        else:
            deferred.append(p)

    # 상한 때문에 top_n 을 못 채웠으면 제한을 풀고 score 순으로 채운다.
    for p in deferred:
        if len(chosen) >= top_n:
            break
        chosen.append(p)
        per_stop[p.board.stop_id] += 1

    chosen.sort(key=lambda p: p.score)
    result = chosen[:top_n]

    # 서로 다른 정류장이 min_distinct_stops 개 이상 포함되도록 보장한다.
    distinct = {p.board.stop_id for p in result}
    if len(distinct) < min_distinct_stops:
        for cand in ordered:
            if len(distinct) >= min_distinct_stops:
                break
            if cand.board.stop_id in distinct:
                continue
            # 가장 많이 차지한 정류장의 최하위 항목을 내보낸다.
            counts = Counter(p.board.stop_id for p in result)
            crowded = counts.most_common(1)[0][0]
            victim = max(
                (p for p in result if p.board.stop_id == crowded),
                key=lambda p: p.score,
            )
            result[result.index(victim)] = cand
            distinct = {p.board.stop_id for p in result}
        result.sort(key=lambda p: p.score)

    return result
