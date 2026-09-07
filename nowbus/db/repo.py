"""정적 데이터 조회. 요청 경로에서 외부 API 를 절대 부르지 않는다 (설계서 §8 원칙 1)."""

from __future__ import annotations

import pathlib
import sqlite3
from math import cos, radians

from nowbus.core.walking import haversine_m
from nowbus.models import Combo, Route, RouteStopRow, Stop

SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


class StaticRepo:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.conn = connect(db_path)

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ 정류장
    def stops_within(self, lat: float, lon: float, radius_m: float) -> list[Stop]:
        """bounding box 로 1차 거른 뒤 haversine 으로 2차 필터.

        SQLite 에 삼각함수를 맡기지 않는다. bbox 는 인덱스를 타고, 남은 소수만
        파이썬에서 정확히 잰다.
        """
        dlat = radius_m / 111_195.0
        # 위도가 높을수록 경도 1도의 실거리가 짧아지므로 bbox 폭을 넓혀야 한다.
        dlon = radius_m / (111_195.0 * max(cos(radians(lat)), 1e-6))
        rows = self.conn.execute(
            "SELECT stop_id, ars_id, name, lat, lon FROM stop "
            "WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            (lat - dlat, lat + dlat, lon - dlon, lon + dlon),
        ).fetchall()
        found = [(haversine_m(lat, lon, r["lat"], r["lon"]), r) for r in rows]
        return [
            Stop(r["stop_id"], r["ars_id"], r["name"], r["lat"], r["lon"])
            for d, r in sorted(found, key=lambda x: x[0])
            if d <= radius_m
        ]

    # ------------------------------------------------------------ 직통 조합
    def find_direct_combos(
        self, origin_stop_ids: list[str], dest_stop_ids: list[str]
    ) -> list[Combo]:
        """설계서 §9.1. 프로그램의 심장.

        조건은 seq 증가 하나뿐이다. direction 으로 조인하지 않는다.

        direction 은 상행/하행 코드가 아니라 '버스 앞 행선판' 문자열이고,
        회차 지점에서 바뀔 뿐 차량은 seq 1 -> N 을 연속으로 달린다. 146번
        실물에서 회차점은 강남 한복판(seq 67, 기점에서 18.4km)이고 seq 135 는
        기점으로 되돌아온다. 즉 강남역9번출구(seq 66)에서 타면 4정거장 뒤
        강남역1번출구(seq 70)에 내린다 - direction 이 달라도 유효한 조합이다.
        여기서 조인을 걸면 멀쩡한 후보를 죽인다.

        방향 문제는 seq 비교가 이미 해결한다. 도로 양쪽 정류장은 서로 다른
        stop_id 에 서로 다른 seq 를 가지므로, 반대편에서 타면 seq 가 감소해
        자동으로 탈락한다.

        같은 (route, board, alight) 가 여러 번 나오면 n_stops 최소값만 남긴다
        (계획서 §10-2 회차 노선 대응).
        """
        if not origin_stop_ids or not dest_stop_ids:
            return []
        o = ",".join("?" * len(origin_stop_ids))
        d = ",".join("?" * len(dest_stop_ids))
        rows = self.conn.execute(
            f"""
            SELECT rs1.route_id,
                   rs1.stop_id AS board_stop,
                   rs2.stop_id AS alight_stop,
                   MIN(rs2.seq - rs1.seq) AS n_stops
            FROM route_stop rs1
            JOIN route_stop rs2
              ON rs1.route_id = rs2.route_id
            WHERE rs1.stop_id IN ({o})
              AND rs2.stop_id IN ({d})
              AND rs2.seq > rs1.seq
            GROUP BY rs1.route_id, rs1.stop_id, rs2.stop_id
            """,
            (*origin_stop_ids, *dest_stop_ids),
        ).fetchall()
        return [Combo(r["route_id"], r["board_stop"], r["alight_stop"], r["n_stops"]) for r in rows]

    def get_routes(self, route_ids: list[str]) -> dict[str, Route]:
        if not route_ids:
            return {}
        q = ",".join("?" * len(route_ids))
        rows = self.conn.execute(
            f"SELECT route_id, route_name, route_type, headway_min "
            f"FROM route WHERE route_id IN ({q})",
            route_ids,
        ).fetchall()
        return {
            r["route_id"]: Route(r["route_id"], r["route_name"], r["route_type"], r["headway_min"])
            for r in rows
        }

    def get_stops(self, stop_ids: list[str]) -> dict[str, Stop]:
        if not stop_ids:
            return {}
        q = ",".join("?" * len(stop_ids))
        rows = self.conn.execute(
            f"SELECT stop_id, ars_id, name, lat, lon FROM stop WHERE stop_id IN ({q})",
            stop_ids,
        ).fetchall()
        return {
            r["stop_id"]: Stop(r["stop_id"], r["ars_id"], r["name"], r["lat"], r["lon"])
            for r in rows
        }

    # ------------------------------------------------------------ 즐겨찾기
    def get_place(self, name: str) -> tuple[float, float] | None:
        r = self.conn.execute("SELECT lat, lon FROM place WHERE name = ?", (name,)).fetchone()
        return (r["lat"], r["lon"]) if r else None

    def list_places(self) -> list[tuple[str, float, float]]:
        rows = self.conn.execute("SELECT name, lat, lon FROM place ORDER BY name").fetchall()
        return [(r["name"], r["lat"], r["lon"]) for r in rows]

    def save_place(self, name: str, lat: float, lon: float) -> None:
        self.conn.execute(
            "INSERT INTO place(name, lat, lon) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET lat = excluded.lat, lon = excluded.lon",
            (name, lat, lon),
        )
        self.conn.commit()

    # ------------------------------------------------------------ 수집 배치용
    def upsert_route_stops(self, rows: list[RouteStopRow], city_code: str = "SEOUL") -> None:
        """정류장·노선·경유순서를 한 트랜잭션으로 넣는다."""
        with self.conn:
            self.conn.executemany(
                "INSERT INTO stop(stop_id, ars_id, name, lat, lon, city_code) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(stop_id) DO UPDATE SET "
                "ars_id = excluded.ars_id, name = excluded.name, "
                "lat = excluded.lat, lon = excluded.lon",
                [(r.stop_id, r.ars_id, r.stop_name, r.lat, r.lon, city_code) for r in rows],
            )
            self.conn.executemany(
                "INSERT INTO route(route_id, route_name, city_code) VALUES (?, ?, ?) "
                "ON CONFLICT(route_id) DO UPDATE SET route_name = excluded.route_name",
                list({(r.route_id, r.route_name, city_code) for r in rows}),
            )
            self.conn.executemany(
                "INSERT INTO route_stop(route_id, stop_id, seq, direction) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(route_id, seq) DO UPDATE SET "
                "stop_id = excluded.stop_id, direction = excluded.direction",
                [(r.route_id, r.stop_id, r.seq, r.direction) for r in rows],
            )

    def mark_collected(self, kind: str, key: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO collect_progress(kind, key, done_at) "
                "VALUES (?, ?, datetime('now')) ON CONFLICT(kind, key) DO NOTHING",
                (kind, key),
            )

    def collected_keys(self, kind: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT key FROM collect_progress WHERE kind = ?", (kind,)
        ).fetchall()
        return {r["key"] for r in rows}
