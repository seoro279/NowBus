"""DB 구축 배치 진입점.

python -m nowbus.collectors.build_db data/raw/서울시버스노선별정류소정보.xlsx
"""

from __future__ import annotations

import pathlib
import sys

from nowbus.collectors.seoul_static import parse_route_stop_xlsx
from nowbus.config import settings
from nowbus.db.repo import StaticRepo, init_schema
from nowbus.models import RouteStopRow

CHUNK = 5_000


def build(xlsx_path: str, db_path: str) -> dict[str, int]:
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    repo = StaticRepo(db_path)
    try:
        init_schema(repo.conn)
        buf: list[RouteStopRow] = []
        total = 0
        for row in parse_route_stop_xlsx(xlsx_path):
            buf.append(row)
            if len(buf) >= CHUNK:
                repo.upsert_route_stops(buf)
                total += len(buf)
                print(f"  {total:>7,}행 반영", flush=True)
                buf.clear()
        if buf:
            repo.upsert_route_stops(buf)
            total += len(buf)
        repo.mark_collected("route_stop_xlsx", pathlib.Path(xlsx_path).name)
        return stat(repo) | {"rows": total}
    finally:
        repo.close()


def stat(repo: StaticRepo) -> dict[str, int]:
    q = lambda sql: repo.conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "stops": q("SELECT COUNT(*) FROM stop"),
        "routes": q("SELECT COUNT(*) FROM route"),
        "route_stops": q("SELECT COUNT(*) FROM route_stop"),
    }


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(f"사용법: python -m {__spec__.name} <xlsx 경로> [db 경로]")
    xlsx = sys.argv[1]
    db = sys.argv[2] if len(sys.argv) > 2 else settings.db_path
    print(f"{xlsx}\n  → {db}")
    result = build(xlsx, db)
    print(
        f"\n완료: 정류장 {result['stops']:,} / 노선 {result['routes']:,} / "
        f"경유 {result['route_stops']:,}"
    )


if __name__ == "__main__":
    main()
