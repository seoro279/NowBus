"""오케스트레이션. 설계서 §6.1 의 1~7 단계를 이어붙인다.

repo 와 provider 를 주입받는다. 테스트에서 목으로 갈아끼우기 위함이자,
결과물 형태(D-2)가 또 바뀌어도 이 함수가 그대로 재사용되는 근거다.
FastAPI 든 CLI 든 봇이든 plan_now() 하나만 부른다.
"""

from __future__ import annotations

from dataclasses import replace

from nowbus.config import Settings
from nowbus.core.catchability import pick_boardable, pick_sprint
from nowbus.core.ranking import Weights, rank, score_plan
from nowbus.core.walking import run_minutes, walk_minutes
from nowbus.db.repo import StaticRepo
from nowbus.models import Arrival, Catch, Combo, Plan, PlanSet, Route, Stop
from nowbus.providers.base import ArrivalProvider

Coord = tuple[float, float]


def _walk(cfg: Settings, a: Coord, b: Coord) -> float:
    return walk_minutes(
        a[0],
        a[1],
        b[0],
        b[1],
        speed_m_per_min=cfg.walk_speed_m_per_min,
        detour=cfg.walk_detour,
        calib=cfg.walk_calib,
    )


def _run(cfg: Settings, a: Coord, b: Coord) -> float:
    return run_minutes(
        a[0],
        a[1],
        b[0],
        b[1],
        speed_m_per_min=cfg.run_speed_m_per_min,
        detour=cfg.walk_detour,
        calib=cfg.walk_calib,
    )


def _build(
    *,
    board: Stop,
    alight: Stop,
    route: Route,
    combo: Combo,
    arrival: Arrival,
    catch: Catch,
    to_board_min: float,
    walk_to_board_min: float,
    walk_from_alight_min: float,
    cfg: Settings,
) -> Plan:
    """후보 1건을 조립한다.

    to_board_min 이 '실제로 정류장까지 가는 데 쓰는 시간'이다. 걷는 후보면
    도보 시간이고 뛰는 후보면 뛰는 시간이다. 총 소요시간과 여유는 이 값으로
    재고, walk_to_board_min 은 화면에 '원래 걸으면 N분'을 같이 보여주기 위해
    따로 들고 간다.

    max() 인 이유: 탈 수 있는 버스라면 '이동 + 대기'는 결국 버스 도착시각과
    같다. 반대로 이동이 더 걸리면 이동이 지배한다.
    """
    ride = combo.n_stops * cfg.ride_min_per_stop
    return Plan(
        board=board,
        alight=alight,
        route=route,
        walk_to_board_min=walk_to_board_min,
        walk_from_alight_min=walk_from_alight_min,
        eta_min=arrival.eta_min,
        ride_min=ride,
        total_min=max(arrival.eta_min, to_board_min) + ride + walk_from_alight_min,
        margin_min=arrival.eta_min - to_board_min,
        catch=catch,
        score=0.0,  # rank() / _rank_sprints() 가 채운다
        congestion=arrival.congestion,
        is_last=arrival.is_last,
        is_estimated=arrival.is_estimated,
        run_to_board_min=to_board_min if catch is Catch.RUN else None,
    )


def _rank_sprints(sprints: list[Plan], best: list[Plan], cfg: Settings) -> list[Plan]:
    """뛰는 후보를 걸러 정렬한다. rank() 의 다양성 보정은 여기 쓰지 않는다.

    다양성 보정은 '첫 정류장을 놓쳤을 때의 대안'을 남기는 규칙인데, 뛰는 후보는
    그 자체가 대안이라 같은 논리를 또 적용할 이유가 없다.

    걸러내는 기준 하나: **뛰어서 얻는 게 없으면 보여주지 않는다.** 걸어서 잡는
    최선의 후보보다 총 소요시간이 짧지 않다면 뛸 이유가 없다. 편한 선택지가
    아예 없을 때는(best 가 비었을 때) 전부 남긴다 - 그때는 뛰는 게 유일한 수단이다.
    """
    if not sprints:
        return []
    w = Weights(tight=cfg.w_tight, walk=cfg.w_walk, run=cfg.w_run)
    scored = [replace(p, score=score_plan(p, w)) for p in sprints]

    if best:
        limit = min(p.total_min for p in best)
        scored = [p for p in scored if p.total_min < limit]

    # 같은 (정류장, 노선) 은 하나만. 뛰는 후보에 2차 차량까지 늘어놓을 필요는 없다.
    uniq: dict[tuple[str, str], Plan] = {}
    for p in sorted(scored, key=lambda p: p.score):
        uniq.setdefault((p.board.stop_id, p.route.route_id), p)
    return sorted(uniq.values(), key=lambda p: p.score)[: cfg.sprint_top_n]


async def plan_now(
    origin: Coord,
    dest: Coord,
    repo: StaticRepo,
    provider: ArrivalProvider,
    cfg: Settings,
) -> PlanSet:
    # 1) 양쪽 모두 인근 정류장 후보를 복수로 전개한다. **여기서 자르지 않는다.**
    #    거리순으로 먼저 잘라 버리면 직통이 있는 정류장을 통째로 놓친다. 실측:
    #    강남역 반경 600m 에 정류장 39곳인데 시청행 직통이 있는 곳은 8곳뿐이고,
    #    그 8곳은 거리순 상위 8곳과 거의 겹치지 않는다. 먼저 자르면 노선 9개 중
    #    1개만 남는다. 목적지 쪽은 더 심해서 12곳으로 제한하면 9개 중 2개만 남았다.
    #    전부 넣어도 조합 질의는 3~5ms 다. 자를 이유가 없다.
    origin_stops = repo.stops_within(*origin, cfg.radius_origin_m)
    dest_stops = repo.stops_within(*dest, cfg.radius_dest_m)
    if not origin_stops or not dest_stops:
        return PlanSet([], [])

    # 2) 도보 시간. 뛰는 시간은 상한 안에 드는 정류장에만 매긴다.
    walk_to = {s.stop_id: _walk(cfg, origin, (s.lat, s.lon)) for s in origin_stops}
    walk_from = {s.stop_id: _walk(cfg, (s.lat, s.lon), dest) for s in dest_stops}
    run_to = {
        s.stop_id: r
        for s in origin_stops
        if (r := _run(cfg, origin, (s.lat, s.lon))) <= cfg.max_run_min
    }

    # 3) 직통 조합 전개 (SQLite 한 방)
    combos = repo.find_direct_combos(
        [s.stop_id for s in origin_stops], [s.stop_id for s in dest_stops]
    )
    if not combos:
        return PlanSet([], [])

    # 3-1) 이제 자른다. 상한이 필요한 이유는 하나뿐 - 실시간 API 호출 예산이다.
    #      그러니 '직통이 있는 승차 정류장' 중에서 가까운 순으로 자른다.
    #      origin_stops 가 이미 거리순이므로 순서를 유지한 채 거르면 된다.
    useful = {c.board_stop_id for c in combos}
    boarding = [s for s in origin_stops if s.stop_id in useful][: cfg.max_origin_stops]
    keep = {s.stop_id for s in boarding}
    combos = [c for c in combos if c.board_stop_id in keep]

    # 4) 실시간 도착 병렬 조회. 여기가 유일하게 외부 호출이 나가는 지점이다.
    arrivals = await provider.arrivals_at_stops(boarding)

    stop_by_id = {s.stop_id: s for s in origin_stops} | {s.stop_id: s for s in dest_stops}
    routes = repo.get_routes(list({c.route_id for c in combos}))

    plans: list[Plan] = []
    sprints: list[Plan] = []
    for c in combos:
        board = stop_by_id.get(c.board_stop_id)
        alight = stop_by_id.get(c.alight_stop_id)
        if board is None or alight is None:
            continue

        on_route = [a for a in arrivals.get(c.board_stop_id, []) if a.route_id == c.route_id]
        if not on_route:
            continue  # 이 노선은 지금 이 정류장에 도착 정보가 없다 (운행종료 등)

        w_to = walk_to[c.board_stop_id]
        w_from = walk_from[c.alight_stop_id]
        route = routes.get(c.route_id) or Route(c.route_id, c.route_id)
        common = dict(
            board=board,
            alight=alight,
            route=route,
            combo=c,
            walk_to_board_min=w_to,
            walk_from_alight_min=w_from,
            cfg=cfg,
        )

        # 5) 탑승 가능성 판정. 배차간격은 도착정보 응답에 같이 실려 온다.
        headway = next((a.headway_min for a in on_route if a.headway_min), route.headway_min)
        picked = pick_boardable(on_route, w_to, cfg.m_safe_min, headway)
        if picked is not None:
            arrival, catch = picked
            plans.append(_build(arrival=arrival, catch=catch, to_board_min=w_to, **common))

        # 5-1) 걸어선 놓치지만 뛰면 잡히는 차. 5) 와 **독립적으로** 본다.
        #      둘 다 나오는 것이 정상이다 - '지금 뛰면 1분 뒤 차, 안 뛰면 9분 뒤 차'.
        r_to = run_to.get(c.board_stop_id)
        if r_to is not None:
            got = pick_sprint(on_route, w_to, r_to)
            if got is not None:
                arrival, catch = got
                sprints.append(_build(arrival=arrival, catch=catch, to_board_min=r_to, **common))

    # 6) 점수화 → 정렬 → 다양성 보정 → 상위 N
    best = rank(
        plans,
        Weights(tight=cfg.w_tight, walk=cfg.w_walk, run=cfg.w_run),
        top_n=cfg.top_n,
        max_per_stop=cfg.max_per_stop,
        min_distinct_stops=cfg.min_distinct_stops,
    )
    return PlanSet(best=best, sprint=_rank_sprints(sprints, best, cfg))
