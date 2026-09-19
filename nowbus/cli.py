"""개발·검증용 CLI.

    nowbus db build <xlsx>       정적 데이터 적재
    nowbus db stat               적재 현황
    nowbus search 강남역          장소 이름으로 좌표 찾기
    nowbus place add 집           검색해서 고른 뒤 등록
    nowbus place add 집 37.5 127.0   좌표를 이미 알 때
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
from nowbus.models import Catch, PlaceHit, Plan, PlanSet

app = typer.Typer(add_completion=False, help="지금 나가면 탈 수 있는 버스를 찾는다.")
db_app = typer.Typer(help="정적 데이터 관리")
place_app = typer.Typer(help="즐겨찾기 장소")
app.add_typer(db_app, name="db")
app.add_typer(place_app, name="place")

MARK = {
    Catch.SAFE: "[SAFE ]",
    Catch.TIGHT: "[TIGHT]",
    Catch.RUN: "[RUN  ]",
    Catch.MISS: "[MISS ]",
}


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


@app.command("stops")
def stops(
    keyword: str = typer.Argument(..., help="정류장 이름 일부. 예: 상계, 강남역"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """정류장을 이름으로 찾아 좌표를 보여준다.

    즐겨찾기에 넣을 좌표를 구할 때 쓴다. 집 바로 앞 정류장을 찾은 뒤, 그 좌표를
    그대로 쓰지 말고 집 위치로 조금 옮기는 편이 정확하다 - 정류장 좌표를 그대로
    쓰면 도보 시간이 0 으로 잡혀 탑승 가능성 판정이 무의미해진다.
    """
    repo = _repo(Settings())
    found = repo.search_stops(keyword, limit)
    if not found:
        typer.echo(f"'{keyword}' 를 포함하는 정류장이 없다.")
    for st, n in found:
        typer.echo(f"  {st.lat:.6f},{st.lon:.6f}   {st.name}  (ARS {st.ars_id}, 노선 {n}개)")
    repo.close()


# ---------------------------------------------------------------- 장소 검색
def _search_places(repo, cfg: Settings, query: str, limit: int) -> list[PlaceHit]:
    """지오코더를 만들어 한 번 검색한다. 카카오 키가 없으면 정류장 이름만 본다."""
    import httpx

    from nowbus.providers.geocode import build_geocoder

    async def go() -> list[PlaceHit]:
        if not cfg.kakao_rest_key:
            return await build_geocoder(repo, "", None).search(query, limit)
        async with httpx.AsyncClient(timeout=cfg.http_timeout_sec) as client:
            return await build_geocoder(repo, cfg.kakao_rest_key, client).search(query, limit)

    return asyncio.run(go())


def _show_hits(hits: list[PlaceHit]) -> None:
    for i, h in enumerate(hits, 1):
        tail = f"  [{h.source}]" if h.source != "stop" else "  [정류장]"
        typer.echo(f"  {i}. {h.name}{tail}")
        typer.echo(f"     {h.lat:.6f},{h.lon:.6f}   {h.address or ''}")


@app.command("search")
def search(
    query: str = typer.Argument(..., help="장소 이름·주소. 예: 강남역, 세종대로 110"),
    limit: int = typer.Option(8, "--limit", "-n"),
) -> None:
    """장소를 이름으로 찾아 좌표를 보여준다 [F-19].

    NOWBUS_KAKAO_REST_KEY 가 있으면 건물·상호·주소까지 찾고, 없으면 DB 에 있는
    정류장 이름만 본다. 키 없이도 동작하는 게 정상이다.
    """
    cfg = Settings()
    repo = _repo(cfg)
    try:
        hits = _search_places(repo, cfg, query, limit)
    finally:
        repo.close()
    if not hits:
        typer.echo(f"'{query}' 를 찾지 못했다.")
        if not cfg.kakao_rest_key:
            typer.echo("  정류장 이름만 검색했다. 건물·상호로 찾으려면 카카오 키가 필요하다.")
        return
    _show_hits(hits)


# ---------------------------------------------------------------- place
@place_app.command("add")
def place_add(
    name: str = typer.Argument(..., help="즐겨찾기 이름. 예: 집"),
    lat: float = typer.Argument(None, help="생략하면 이름(또는 --query)으로 검색한다"),
    lon: float = typer.Argument(None),
    query: str = typer.Option(None, "--query", "-q", help="검색어. 생략하면 이름을 그대로 쓴다"),
) -> None:
    """즐겨찾기를 등록한다. 좌표를 모르면 검색해서 고른다.

    좌표를 직접 넣는 길을 남겨둔 이유: 검색 결과가 정류장뿐일 때 그 좌표를
    그대로 쓰면 도보 시간이 0 으로 잡혀 판정이 무의미해진다. 그럴 때는 지도에서
    집 위치로 조금 옮긴 좌표를 직접 주는 편이 정확하다.
    """
    cfg = Settings()
    repo = _repo(cfg)
    try:
        if lat is None or lon is None:
            hits = _search_places(repo, cfg, query or name, 8)
            if not hits:
                raise typer.BadParameter(f"'{query or name}' 를 찾지 못했다. 좌표를 직접 줄 것.")
            _show_hits(hits)
            pick = typer.prompt("번호", default="1")
            try:
                chosen = hits[int(pick) - 1]
            except (ValueError, IndexError) as e:
                raise typer.BadParameter(f"1~{len(hits)} 중에서 고를 것.") from e
            lat, lon = chosen.lat, chosen.lon
            if chosen.source == "stop":
                typer.secho(
                    "  주의: 정류장 좌표다. 이 위치를 출발지로 쓰면 도보 시간이 0 에 가깝게"
                    " 잡힌다.",
                    fg=typer.colors.YELLOW,
                )
        repo.save_place(name, lat, lon)
        typer.echo(f"등록: {name} ({lat:.6f}, {lon:.6f})")
    finally:
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
def _one(i: int, p: Plan, now: datetime) -> None:
    arrive = now + timedelta(minutes=p.total_min)
    extra = []
    if p.congestion:
        extra.append(f"혼잡도 {p.congestion}")
    if p.is_last:
        extra.append("막차")
    if p.is_estimated:
        extra.append("배차간격 추정")
    tail = f"  ({', '.join(extra)})" if extra else ""
    if p.run_to_board_min is not None:
        move = f"뛰어서 {p.run_to_board_min:.0f}분 (걸으면 {p.walk_to_board_min:.0f}분)"
    else:
        move = f"도보 {p.walk_to_board_min:.0f}분"
    typer.echo(f"{i}. {MARK[p.catch]} {p.board.name}  {move}")
    typer.echo(
        f"      {p.route.route_name}번  {p.eta_min:.0f}분 후 도착"
        f"   여유 {p.margin_min:+.0f}분{tail}"
    )
    typer.echo(f"      -> {p.alight.name} 하차, 도보 {p.walk_from_alight_min:.0f}분")
    typer.echo(f"      {arrive:%H:%M} 도착 예상 (총 {p.total_min:.0f}분)\n")


def _render(found: PlanSet, origin: str, dest: str, elapsed_ms: float) -> None:
    now = datetime.now()
    typer.echo(f"\n{origin} -> {dest}   {now:%H:%M} 기준  ({elapsed_ms:.0f}ms)\n")
    if not found.best and not found.sprint:
        typer.echo("  지금 걸어서 탈 수 있는 직통 버스가 없다.")
        typer.echo("  --radius 를 올려보거나, 목적지 좌표를 확인할 것.\n")
        return

    if found.sprint:
        typer.echo("  [지금 뛰면 잡을 수 있다]\n")
        for i, p in enumerate(found.sprint, 1):
            _one(i, p, now)

    if not found.best:
        typer.echo("  걸어서 잡을 수 있는 버스는 없다.\n")
        return
    if all(p.catch is Catch.MISS for p in found.best):
        typer.echo("  ! 지금 나가면 다 놓친다.\n")
    if found.sprint:
        typer.echo("  [걸어서 잡을 수 있다]\n")

    for i, p in enumerate(found.best, 1):
        _one(i, p, now)

    stops = len({p.board.stop_id for p in found.best})
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
            found = await plan_now(o, d, repo, provider, cfg)
            return found, (time.perf_counter() - t) * 1000

    try:
        found, ms = asyncio.run(run())
    finally:
        repo.close()
    _render(found, origin, dest, ms)


@app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", help="0.0.0.0 이어야 폰에서 접속된다"),
    port: int = typer.Option(8000),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """개발 서버를 띄운다. 배포는 Cloudflare Tunnel 등으로 HTTPS 를 씌운다."""
    import uvicorn

    cfg = Settings()
    if cfg.api_token == "change-me":
        typer.secho(
            "경고: NOWBUS_API_TOKEN 이 기본값이다. .env 에서 바꿀 것.",
            fg=typer.colors.YELLOW,
        )
    typer.echo(f"http://{host}:{port}  (토큰: X-Token 헤더 또는 ?token=)")
    uvicorn.run("nowbus.web.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
