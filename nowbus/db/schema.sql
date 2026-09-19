-- NowBus 정적 데이터. 설계서 §9.

CREATE TABLE IF NOT EXISTS stop (
    stop_id     TEXT PRIMARY KEY,   -- 내부 정류장 ID
    ars_id      TEXT,               -- 정류장 고유번호(사용자 표시용). 미정차 정류소는 '0'
    name        TEXT NOT NULL,
    lat         REAL NOT NULL,
    lon         REAL NOT NULL,
    city_code   TEXT
);
CREATE INDEX IF NOT EXISTS idx_stop_geo ON stop(lat, lon);

CREATE TABLE IF NOT EXISTS route (
    route_id    TEXT PRIMARY KEY,
    route_name  TEXT NOT NULL,      -- "146", "360"
    route_type  TEXT,               -- 간선/지선/광역 등
    headway_min REAL,               -- 평시 배차간격(참고용)
    city_code   TEXT
);

-- ★ 핵심 테이블. 이게 있어야 직통 조합 탐색이 가능하다.
-- direction 은 상행/하행 코드가 아니라 '행선지 종점명' 문자열이다.
-- 146번 실물 확인: seq 1~68 은 direction='강남역', 69~135 는 '상계주공7단지'.
-- 즉 왕복이 하나의 seq 축에 이어 붙고 회차 지점에서 direction 이 바뀐다.
-- seq 는 노선 전체에서 유일하므로 PK 는 (route_id, seq) 로 충분하다.
CREATE TABLE IF NOT EXISTS route_stop (
    route_id    TEXT NOT NULL,
    stop_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,   -- 경유 순서 (노선 전체에서 유일)
    direction   TEXT,               -- 행선지 종점명. "강남역" 등
    PRIMARY KEY (route_id, seq),
    FOREIGN KEY (route_id) REFERENCES route(route_id),
    FOREIGN KEY (stop_id)  REFERENCES stop(stop_id)
);
CREATE INDEX IF NOT EXISTS idx_rs_stop ON route_stop(stop_id);

CREATE TABLE IF NOT EXISTS place (      -- 즐겨찾기
    name        TEXT PRIMARY KEY,       -- "집", "회사"
    lat         REAL NOT NULL,
    lon         REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS trip_log (   -- 실측 피드백 (F-11)
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at         TEXT,
    stop_id            TEXT,
    predicted_walk_min REAL,
    actual_walk_min    REAL
);

-- 수집 배치의 재개 지점. Phase 1에서 중단 후 이어받기 위해 필요하다.
CREATE TABLE IF NOT EXISTS collect_progress (
    kind        TEXT NOT NULL,      -- 'route_stop' 등
    key         TEXT NOT NULL,      -- route_id 등
    done_at     TEXT,
    PRIMARY KEY (kind, key)
);
