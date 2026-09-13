"""HTTP 라우트. Planner 를 부르고 스키마로 옮겨 담는 것 외에 로직이 없다."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status

from nowbus.core.planner import plan_now
from nowbus.db.repo import StaticRepo
from nowbus.models import Catch, Plan
from nowbus.schemas import Feedback, Place, PlaceCreate, PlanItem, PlanResponse
from nowbus.web.deps import ProviderDep, RepoDep, SettingsDep, TokenDep

router = APIRouter(prefix="/api")


def _resolve(
    repo: StaticRepo, lat: float | None, lon: float | None, name: str | None, what: str
) -> tuple[tuple[float, float], str]:
    """좌표가 오면 그걸 쓰고, 아니면 즐겨찾기에서 찾는다. 라벨도 함께 돌려준다."""
    if lat is not None and lon is not None:
        return (lat, lon), ("현재 위치" if what == "출발" else "지정 좌표")
    if not name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"{what}지가 없다. 좌표(lat/lon) 또는 즐겨찾기 이름을 줄 것.",
        )
    found = repo.get_place(name)
    if found is None:
        known = [n for n, _, _ in repo.list_places()]
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"'{name}' 즐겨찾기가 없다. 등록된 곳: {known}"
        )
    return found, name


def _to_item(rank: int, p: Plan, now: datetime) -> PlanItem:
    return PlanItem(
        rank=rank,
        board_stop_name=p.board.name,
        board_stop_ars=p.board.ars_id,
        board_lat=p.board.lat,
        board_lon=p.board.lon,
        walk_to_board_min=round(p.walk_to_board_min),
        route_name=p.route.route_name,
        eta_min=round(p.eta_min),
        margin_min=round(p.margin_min),
        catch=p.catch.value,
        alight_stop_name=p.alight.name,
        walk_from_alight_min=round(p.walk_from_alight_min),
        ride_min=round(p.ride_min),
        total_min=round(p.total_min),
        arrive_at=(now + timedelta(minutes=p.total_min)).strftime("%H:%M"),
        congestion=p.congestion,
        is_last=p.is_last,
    )


def _warning(plans: list[Plan]) -> str | None:
    if not plans:
        return "지금 걸어서 탈 수 있는 직통 버스가 없어요"
    if all(p.catch is Catch.MISS for p in plans):
        return "지금 나가면 다 놓쳐요"
    return None


@router.get("/plan", response_model=PlanResponse)
async def get_plan(
    repo: RepoDep,
    provider: ProviderDep,
    settings: SettingsDep,
    _: TokenDep,
    lat: float | None = Query(None, ge=-90, le=90),
    lon: float | None = Query(None, ge=-180, le=180),
    to: str | None = Query(None, description="목적지 즐겨찾기 이름"),
    to_lat: float | None = Query(None, ge=-90, le=90),
    to_lon: float | None = Query(None, ge=-180, le=180),
    from_: str | None = Query(None, alias="from", description="GPS 실패 시 폴백"),
    radius: int | None = Query(None, ge=100, le=2000, description="출발 반경(m). 재검색용"),
) -> PlanResponse:
    origin, origin_label = _resolve(repo, lat, lon, from_, "출발")
    dest, dest_label = _resolve(repo, to_lat, to_lon, to, "목적")

    cfg = settings
    if radius:
        # 결과 0건일 때 프론트가 반경을 넓혀 다시 묻는다 (설계서 §10.3).
        cfg = settings.model_copy(update={"radius_origin_m": radius, "radius_dest_m": radius + 100})

    now = datetime.now()
    plans = await plan_now(origin, dest, repo, provider, cfg)
    return PlanResponse(
        origin_label=origin_label,
        dest_label=dest_label,
        departed_at=now.strftime("%H:%M"),
        generated_at=now.astimezone().isoformat(),
        items=[_to_item(i, p, now) for i, p in enumerate(plans, 1)],
        warning=_warning(plans),
    )


@router.get("/places", response_model=list[Place])
async def list_places(repo: RepoDep, _: TokenDep) -> list[Place]:
    return [Place(name=n, lat=la, lon=lo) for n, la, lo in repo.list_places()]


@router.post("/places", response_model=Place, status_code=status.HTTP_201_CREATED)
async def create_place(body: PlaceCreate, repo: RepoDep, _: TokenDep) -> Place:
    repo.save_place(body.name, body.lat, body.lon)
    return Place(name=body.name, lat=body.lat, lon=body.lon)


@router.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def post_feedback(body: Feedback, repo: RepoDep, _: TokenDep) -> None:
    """실측 도보시간 기록 [F-11]. 나중에 CALIB 보정에 쓴다."""
    with repo.conn:
        repo.conn.execute(
            "INSERT INTO trip_log(created_at, stop_id, predicted_walk_min, actual_walk_min) "
            "VALUES (datetime('now'), ?, ?, ?)",
            (body.stop_id, body.predicted_walk_min, body.actual_walk_min),
        )
