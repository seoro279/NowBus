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

        **승차 pass 마다 한 건**이다. 같은 정류장을 두 번 지나는 노선(서울 718개 중
        224개)은 지나는 횟수만큼 조합이 나오고, 각각 그 pass 에서 가장 가까운 하차
        지점까지의 정거장 수를 갖는다 (계획서 §10-2 회차 노선 대응).

        예전에는 (route, board, alight) 당 최소값 하나만 남겼다. 그러면 실물 5616번
        난곡우체국사거리(seq 14, 87)에서 첫 번째로 지나는 차도 '20정거장' 으로
        계산했다 - 실제로는 목동을 다 돌고 93정거장이다. 오목교역처럼 첫 pass 에서만
        가는 목적지는 둘째 pass 로 오는 차(가산행)를 태웠다. 어느 pass 로 오는 차인지는
        도착정보의 staOrd 로 플래너가 가린다.
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
                   MIN(rs2.seq - rs1.seq) AS n_stops,
                   rs1.seq AS board_seq
            FROM route_stop rs1
            JOIN route_stop rs2
              ON rs1.route_id = rs2.route_id
            WHERE rs1.stop_id IN ({o})
              AND rs2.stop_id IN ({d})
              AND rs2.seq > rs1.seq
            GROUP BY rs1.route_id, rs1.stop_id, rs1.seq, rs2.stop_id
            """,
            (*origin_stop_ids, *dest_stop_ids),
        ).fetchall()
        return [
            Combo(r["route_id"], r["board_stop"], r["alight_stop"], r["n_stops"], r["board_seq"])
            for r in rows
        ]

    def stop_passes(self, keys: list[tuple[str, str]]) -> dict[tuple[str, str], set[int]]:
        """(route_id, stop_id) → 그 노선이 그 정류장을 지나는 seq 전부.

        두 개 이상이면 '같은 정류장을 두 번 지나는' 곳이다. 플래너가 도착정보를
        pass 와 짝지을지 정하는 데 쓴다. 조합에는 목적지에 가는 pass 만 있으므로
        조합만 보고는 알 수 없다.
        """
        if not keys:
            return {}
        routes = list({r for r, _ in keys})
        stops = list({s for _, s in keys})
        rows = self.conn.execute(
            f"""
            SELECT route_id, stop_id, seq FROM route_stop
            WHERE route_id IN ({",".join("?" * len(routes))})
              AND stop_id IN ({",".join("?" * len(stops))})
            """,
            (*routes, *stops),
        ).fetchall()
        want = set(keys)
        out: dict[tuple[str, str], set[int]] = {}
        for r in rows:
            k = (r["route_id"], r["stop_id"])
            if k in want:
                out.setdefault(k, set()).add(r["seq"])
        return out

    def board_context(
        self, keys: list[tuple[str, int]]
    ) -> dict[tuple[str, int], tuple[str | None, str | None]]:
        """(route_id, 승차 seq) → (다음 정류장 이름, 행선지).

        길 양쪽에 이름이 같은 정류장이 있으면 카드만 봐서는 어느 쪽인지 모른다.
        실물 146번에서 '공릉시장' 은 seq 29(다음 태릉입구역3번출구, 강남역행)와
        seq 107(다음 공릉역1번출구, 상계주공7단지행) 두 곳이다. 실제 정류장
        표지판이 '○○ 방면' 을 다음 정류장으로 적으므로 그걸 돌려준다.

        direction 은 표시용으로만 읽는다. 조합 판정에 쓰면 안 된다 (find_direct_combos
        독스트링과 인계 메모 §2-1 참조).
        """
        if not keys:
            return {}
        out: dict[tuple[str, int], tuple[str | None, str | None]] = {}
        # 키가 수십 개라 한 방에 묶는다. VALUES 로 임시 테이블을 만들어 조인한다.
        values = ",".join("(?, ?)" for _ in keys)
        rows = self.conn.execute(
            f"""
            WITH k(route_id, seq) AS (VALUES {values})
            SELECT k.route_id, k.seq, cur.direction, s.name AS next_name
            FROM k
            JOIN route_stop cur ON cur.route_id = k.route_id AND cur.seq = k.seq
            LEFT JOIN route_stop nxt ON nxt.route_id = k.route_id AND nxt.seq = k.seq + 1
            LEFT JOIN stop s ON s.stop_id = nxt.stop_id
            """,
            [v for key in keys for v in key],
        ).fetchall()
        for r in rows:
            out[(r["route_id"], r["seq"])] = (r["next_name"], r["direction"])
        return out

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

    def search_stops(self, keyword: str, limit: int = 20) -> list[tuple[Stop, int]]:
        """정류장 이름 부분 검색. (정류장, 경유 노선 수) 를 돌려준다.

        지도 앱에서 좌표를 캐내는 것보다 이게 빠르다. 12,897곳이 이미 DB 에 있다.

        정렬은 '이름이 얼마나 정확히 맞는지'가 먼저다. 노선 수만으로 줄 세우면
        "상계주공" 을 찾을 때 '노원역5번출구.상계주공6단지' 가 '상계주공10단지'
        앞에 온다 - 검색어로 시작하는 이름이 사람이 찾던 것에 가깝다.
        """
        rows = self.conn.execute(
            "SELECT s.stop_id, s.ars_id, s.name, s.lat, s.lon, "
            "       COUNT(DISTINCT rs.route_id) AS n_routes "
            "FROM stop s LEFT JOIN route_stop rs ON rs.stop_id = s.stop_id "
            "WHERE s.name LIKE ? "
            "GROUP BY s.stop_id "
            "ORDER BY (s.name = ?) DESC, (s.name LIKE ?) DESC, n_routes DESC, s.name "
            "LIMIT ?",
            (f"%{keyword}%", keyword, f"{keyword}%", limit),
        ).fetchall()
        return [
            (Stop(r["stop_id"], r["ars_id"], r["name"], r["lat"], r["lon"]), r["n_routes"])
            for r in rows
        ]

    # ------------------------------------------------------------ 즐겨찾기
    def get_place(self, name: str) -> tuple[float, float] | None:
        r = self.conn.execute("SELECT lat, lon FROM place WHERE name = ?", (name,)).fetchone()
        return (r["lat"], r["lon"]) if r else None

    def list_places(self) -> list[tuple[str, float, float]]:
        rows = self.conn.execute("SELECT name, lat, lon FROM place ORDER BY name").fetchall()
        return [(r["name"], r["lat"], r["lon"]) for r in rows]

    def delete_place(self, name: str) -> bool:
        """지웠으면 True, 그런 이름이 없었으면 False.

        '있었는지'를 돌려주는 이유: 라우트가 404 와 204 를 가려야 한다. 오타로
        지운 줄 알고 넘어가면 사용자는 왜 목록에 그대로 있는지 알 수 없다.
        """
        with self.conn:
            cur = self.conn.execute("DELETE FROM place WHERE name = ?", (name,))
        return cur.rowcount > 0

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
