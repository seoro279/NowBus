"""개발·검증용 CLI.

    nowbus db build <xlsx>       정적 데이터 적재
    nowbus db stat               적재 현황
    nowbus place add 집 37.5 127.0
    nowbus place list
    nowbus plan 집 회사           서버 없이 코어 로직 직접 확인

설계서 §8 원칙 3 덕분에 이 파일은 plan_now() 를 부르기만 한다. 서버가 생겨도
유지 비용이 거의 0 이라 폐기하지 않고 디버깅 도구로 남긴다.
"""

from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timedelta

import typer

from nowbus.config import Settings
from nowbus.models import Catch, Plan

app = typer.Typer(add_completion=False, help="지금 나가면 탈 수 있는 버스를 찾는다.")
db_app = typer.Typer(help="정적 데이터 관리")
place_app = typer.Typer(help="즐겨찾기 장소")
app.add_typer(db_app, name="db")
app.add_typer(place_app, name="place")

MARK = {Catch.SAFE: "[SAFE ]", Catch.TIGHT: "[TIGHT]", Catch.MISS: "[MISS ]"}


def _repo(cfg: Settings):
    """DB 를 열되, 비어 있으면 친절하게 알려준다 (계획서 §10-13)."""
    from nowbus.db.repo import StaticRepo

    if not pathlib.Path(cfg.db_path).exists():
        raise typer.BadParameter(
            f"DB 가 없다: {cfg.db_path}\n  먼저 실행: nowbus db build <xlsx 경로>"
        )
    repo = StaticRepo(cfg.db_path)
    n = repo.conn.execute("SELECT COUNT(*) FROM route_stop").fetchone()[0]
    if n == 0:
        raise typer.BadParameter(
            "route_stop 이 비어 있다.\n  먼저 실행: nowbus db build <xlsx 경로>"
        )
    return repo


def _coord(repo, text: str) -> tuple[float, float]:
    """'37.5,127.0' 형태면 좌표로, 아니면 즐겨찾기 이름으로 읽는다."""
    if "," in text:
        try:
            lat, lon = (float(v) for v in text.split(",", 1))
            return lat, lon
        except ValueError:
            pass
    found = repo.get_place(text)
    if found is None:
        known = [n for n, _, _ in repo.list_places()]
        raise typer.BadParameter(
            f"'{text}' 를 찾을 수 없다. 즐겨찾기: {known or '(없음)'}\n"
            f"  좌표로 직접 주려면: 37.4979,127.0276"
        )
    return found


# ---------------------------------------------------------------- db
@db_app.command("build")
def db_build(
    xlsx: str = typer.Argument(..., help="서울시 버스노선별 정류소정보 xlsx"),
    db: str = typer.Option(None, help="출력 DB 경로"),
) -> None:
    from nowbus.collectors.build_db import build

    cfg = Settings()
    result = build(xlsx, db or cfg.db_path)
    typer.echo(
        f"정류장 {result['stops']:,} / 노선 {result['routes']:,} / 경유 {result['route_stops']:,}"
    )


@db_app.command("stat")
def db_stat() -> None:
    from nowbus.collectors.build_db import stat

    cfg = Settings()
    repo = _repo(cfg)
    s = stat(repo)
    typer.echo(f"{cfg.db_path}")
    typer.echo(f"  정류장 {s['stops']:,}")
    typer.echo(f"  노선   {s['routes']:,}")
    typer.echo(f"  경유   {s['route_stops']:,}")
    repo.close()


# ---------------------------------------------------------------- place
@place_app.command("add")
def place_add(name: str, lat: float, lon: float) -> None:
    repo = _repo(Settings())
    repo.save_place(name, lat, lon)
    typer.echo(f"등록: {name} ({lat}, {lon})")
    repo.close()


@place_app.command("list")
def place_list() -> None:
    repo = _repo(Settings())
    rows = repo.list_places()
    if not rows:
        typer.echo("등록된 장소가 없다.  nowbus place add 집 37.5 127.0")
    for name, lat, lon in rows:
        typer.echo(f"  {name:<10} {lat}, {lon}")
    repo.close()


# ---------------------------------------------------------------- plan
def _render(plans: list[Plan], origin: str, dest: str, elapsed_ms: float) -> None:
    now = datetime.now()
    typer.echo(f"\n{origin} -> {dest}   {now:%H:%M} 기준  ({elapsed_ms:.0f}ms)\n")
    if not plans:
        typer.echo("  지금 걸어서 탈 수 있는 직통 버스가 없다.")
        typer.echo("  --radius 를 올려보거나, 목적지 좌표를 확인할 것.\n")
        return
    if all(p.catch is Catch.MISS for p in plans):
        typer.echo("  ! 지금 나가면 다 놓친다.\n")

    for i, p in enumerate(plans, 1):
        arrive = now + timedelta(minutes=p.total_min)
        extra = []
        if p.congestion:
            extra.append(f"혼잡도 {p.congestion}")
        if p.is_last:
            extra.append("막차")
        tail = f"  ({', '.join(extra)})" if extra else ""
        typer.echo(f"{i}. {MARK[p.catch]} {p.board.name}  도보 {p.walk_to_board_min:.0f}분")
        typer.echo(
            f"      {p.route.route_name}번  {p.eta_min:.0f}분 후 도착"
            f"   여유 {p.margin_min:+.0f}분{tail}"
        )
        typer.echo(f"      -> {p.alight.name} 하차, 도보 {p.walk_from_alight_min:.0f}분")
        typer.echo(f"      {arrive:%H:%M} 도착 예상 (총 {p.total_min:.0f}분)\n")

    stops = len({p.board.stop_id for p in plans})
    typer.echo(f"  서로 다른 승차 정류장 {stops}곳\n")


@app.command("plan")
def plan(
    origin: str = typer.Argument(..., help="즐겨찾기 이름 또는 '위도,경도'"),
    dest: str = typer.Argument(..., help="즐겨찾기 이름 또는 '위도,경도'"),
    radius: int = typer.Option(None, "--radius", help="출발 반경(m)"),
    top: int = typer.Option(None, "--top", help="출력 개수"),
    m_safe: float = typer.Option(None, "--m-safe", help="안전 여유(분)"),
) -> None:
    """실제 도착정보를 조회해 후보를 보여준다. API 키가 필요하다."""
    import time

    import httpx

    from nowbus.core.planner import plan_now
    from nowbus.providers.seoul import SeoulProvider

    cfg = Settings()
    if radius:
        cfg = cfg.model_copy(update={"radius_origin_m": radius})
    if top:
        cfg = cfg.model_copy(update={"top_n": top})
    if m_safe is not None:
        cfg = cfg.model_copy(update={"m_safe_min": m_safe})
    # 오프라인으로 확인 가능한 것부터 검사한다. 키 검사를 먼저 하면 장소 오타가
    # 키 에러에 가려져서, 키가 없는 동안에는 오타를 영영 못 찾는다.
    repo = _repo(cfg)
    o, d = _coord(repo, origin), _coord(repo, dest)
    if not cfg.seoul_api_key:
        repo.close()
        raise typer.BadParameter("NOWBUS_SEOUL_API_KEY 가 비어 있다. .env 를 확인할 것.")

    async def run():
        async with httpx.AsyncClient(timeout=cfg.http_timeout_sec) as client:
            provider = SeoulProvider(cfg.seoul_api_key, client, cache_ttl_sec=cfg.cache_ttl_sec)
            t = time.perf_counter()
            plans = await plan_now(o, d, repo, provider, cfg)
            return plans, (time.perf_counter() - t) * 1000

    try:
        plans, ms = asyncio.run(run())
    finally:
        repo.close()
    _render(plans, origin, dest, ms)


if __name__ == "__main__":
    app()
