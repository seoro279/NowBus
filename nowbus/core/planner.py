"""오케스트레이션. 설계서 §6.1 의 1~7 단계를 이어붙인다.

repo 와 provider 를 주입받는다. 테스트에서 목으로 갈아끼우기 위함이자,
결과물 형태(D-2)가 또 바뀌어도 이 함수가 그대로 재사용되는 근거다.
FastAPI 든 CLI 든 봇이든 plan_now() 하나만 부른다.
"""

from __future__ import annotations

from nowbus.config import Settings
from nowbus.core.catchability import pick_boardable
from nowbus.core.ranking import Weights, rank
from nowbus.core.walking import walk_minutes
from nowbus.db.repo import StaticRepo
from nowbus.models import Plan, Route
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


async def plan_now(
    origin: Coord,
    dest: Coord,
    repo: StaticRepo,
    provider: ArrivalProvider,
    cfg: Settings,
) -> list[Plan]:
    # 1) 양쪽 모두 인근 정류장 후보를 복수로 전개한다.
    #    stops_within 은 거리순 정렬이므로 앞에서 자르면 가까운 것부터 남는다.
    #    상한이 없으면 강남 같은 곳에서 20곳이 잡혀 실시간 API 를 20번 부르게 된다.
    origin_stops = repo.stops_within(*origin, cfg.radius_origin_m)[: cfg.max_origin_stops]
    dest_stops = repo.stops_within(*dest, cfg.radius_dest_m)[: cfg.max_dest_stops]
    if not origin_stops or not dest_stops:
        return []

    # 2) 도보 시간
    walk_to = {s.stop_id: _walk(cfg, origin, (s.lat, s.lon)) for s in origin_stops}
    walk_from = {s.stop_id: _walk(cfg, (s.lat, s.lon), dest) for s in dest_stops}

    # 3) 직통 조합 전개 (SQLite 한 방)
    combos = repo.find_direct_combos(
        [s.stop_id for s in origin_stops], [s.stop_id for s in dest_stops]
    )
    if not combos:
        return []

    # 4) 실시간 도착 병렬 조회.
    #    조합에 실제로 등장하는 정류장만 부른다. 직통이 없는 정류장까지 부르면
    #    호출 예산만 태운다.
    by_id = {s.stop_id: s for s in origin_stops}
    boarding = [by_id[sid] for sid in {c.board_stop_id for c in combos} if sid in by_id]
    arrivals = await provider.arrivals_at_stops(boarding)

    stop_by_id = {s.stop_id: s for s in origin_stops} | {s.stop_id: s for s in dest_stops}
    routes = repo.get_routes(list({c.route_id for c in combos}))

    plans: list[Plan] = []
    for c in combos:
        board = stop_by_id.get(c.board_stop_id)
        alight = stop_by_id.get(c.alight_stop_id)
        if board is None or alight is None:
            continue

        on_route = [a for a in arrivals.get(c.board_stop_id, []) if a.route_id == c.route_id]
        if not on_route:
            continue  # 이 노선은 지금 이 정류장에 도착 정보가 없다 (운행종료 등)

        w_to = walk_to[c.board_stop_id]
        route = routes.get(c.route_id) or Route(c.route_id, c.route_id)

        # 5) 탑승 가능성 판정. 배차간격은 도착정보 응답에 같이 실려 온다.
        headway = next((a.headway_min for a in on_route if a.headway_min), route.headway_min)
        picked = pick_boardable(on_route, w_to, cfg.m_safe_min, headway)
        if picked is None:
            continue
        arrival, catch = picked

        # 6) 총 소요시간.
        #    max() 인 이유: 탈 수 있는 버스라면 '도보 + 대기'는 결국 버스 도착시각과
        #    같다. 반대로 도보가 더 걸리면 도보가 지배한다.
        w_from = walk_from[c.alight_stop_id]
        ride = c.n_stops * cfg.ride_min_per_stop
        total = max(arrival.eta_min, w_to) + ride + w_from

        plans.append(
            Plan(
                board=board,
                alight=alight,
                route=route,
                walk_to_board_min=w_to,
                walk_from_alight_min=w_from,
                eta_min=arrival.eta_min,
                ride_min=ride,
                total_min=total,
                margin_min=arrival.eta_min - w_to,
                catch=catch,
                score=0.0,  # rank() 가 채운다
                congestion=arrival.congestion,
                is_last=arrival.is_last,
            )
        )

    # 7) 점수화 → 정렬 → 다양성 보정 → 상위 N
    return rank(
        plans,
        Weights(tight=cfg.w_tight, walk=cfg.w_walk),
        top_n=cfg.top_n,
        max_per_stop=cfg.max_per_stop,
        min_distinct_stops=cfg.min_distinct_stops,
    )
