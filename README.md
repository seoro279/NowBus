# NowBus

> 현재 위치에서 지금 출발했을 때, **실제로 걸어서 닿을 수 있는** 정류장·버스 조합만
> 추려서 목적지 도착이 가장 빠른 순으로 알려주는 개인용 도구.

기존 지도앱은 도보 6분 거리 정류장에 3분 뒤 오는 버스를 "3분 후 도착"이라고 그대로
표시한다. 못 타는 버스다. NowBus는 **catchability(탑승 가능성)** 를 1급 개념으로
올려서, 탈 수 없는 후보를 먼저 걸러낸다.

설계 문서는 `docs/` 참조.

---

## 현재 상태

**Phase 0 진행 중** — 스캐폴딩 완료, 실기기 스모크 테스트 대기.

| Phase | 내용 | 상태 |
|---|---|---|
| 0 | 스캐폴딩 + API 스모크 테스트 | **완료** |
| 1 | 정적 데이터 구축 (`route_stop`) | 수집기 남음 |
| 2 | 오프라인 코어 (도보·직통조합) | **완료** |
| 3 | 실시간 결합 (Provider·판정·랭킹) | 파서 완료, 판정·랭킹 남음 |
| 4 | 서버 API | 대기 |
| 5 | 모바일 웹 UI (PWA) | 대기 |
| 6 | 배포 & 실기기 검증 | 대기 |

---

## 시작하기

### 1. API 키

[공공데이터포털](https://www.data.go.kr/)에서 아래 **세 서비스를 각각 활용신청**한다.
인증키는 계정당 하나지만, 신청하지 않은 서비스는 같은 키로도 호출이 막힌다.

| 서비스 | ID | 쓰임 |
|---|---|---|
| 서울특별시_노선정보조회 | [15000193](https://www.data.go.kr/data/15000193/openapi.do) | 노선별 경유 정류장 순서 (F-09) |
| 서울특별시_정류소정보조회 | [15000303](https://www.data.go.kr/data/15000303/openapi.do) | 정류소 목록·좌표 (F-01) |
| 서울특별시_버스도착정보조회 | [15000314](https://www.data.go.kr/dataset/15000314/openapi.do) | 실시간 도착 (F-04) |

셋 다 실제 호출은 서울시 TOPIS(`ws.bus.go.kr`)로 나간다. 포털은 키만 중개한다.

### 2. 설정

```bash
cp .env.example .env
# NOWBUS_SEOUL_API_KEY 에 '일반 인증키(Decoding)' 를 넣는다
```

`.env` 는 절대 커밋하지 않는다 (`.gitignore` 에 있다).

### 3. 스모크 테스트

```bash
uv sync                              # 또는 pip install -e '.[dev]'
uv run python scripts/smoke.py
```

이 스크립트가 확정하는 것:

- 발급받은 키가 **Encoding / Decoding** 중 어느 방식으로 동작하는지
- 세 서비스의 오퍼레이션명과 실제 응답 필드명
- `getStaionByRoute` 응답에 `seq` / `direction` 이 채워지는지
  (여기가 비면 상하행 구분을 다른 방법으로 해야 한다 — 아래 함정 1번)

응답 원본은 `tests/fixtures/` 에 저장된다. 파싱 테스트와 스펙 변경 감지에 쓴다.

> ⚠️ **원격 개발 컨테이너에서는 실행되지 않는다.** `ws.bus.go.kr` 과
> `apis.data.go.kr` 이 이그레스 정책에 403으로 막혀 있다. 로컬 PC에서 돌릴 것.

### 4. 테스트

```bash
uv run pytest
uv run ruff check .
```

---

## API 실측 결과 (Phase 0)

추측이 아니라 `tests/fixtures/` 의 실제 응답에서 확인한 것들이다.
스펙이 바뀌면 픽스처에 물린 테스트가 먼저 깨진다.

| 항목 | 실측 |
|---|---|
| 인증키 | **Decoding** 방식으로 호출 (`httpx params=` 에 그대로) |
| `tmX` / `tmY` | 경도 / 위도 (이름과 달리 TM 좌표가 아니다) |
| `traTime1` | **초 단위**. `arrmsg1` 은 사람용 문자열이므로 파싱하지 않는다 |
| `term` | 배차간격(분). 1·2차가 모두 MISS 일 때 3차 추정에 쓴다 |
| `congestion1` | 1~4. **0 은 미측정**이지 '한산함'이 아니다 |
| stop_id | `getStaionByRoute` 의 `<station>` = `getStationByPos` 의 `<stationId>` |

### 함정 세 개 — 전부 실물에서 확인됨

**1. `direction` 은 상행/하행 코드가 아니라 버스 앞 행선판이다. 조인에 쓰면 안 된다.**
146번은 seq 1~68 이 `"강남역"`, 69~135 가 `"상계주공7단지"`. 왕복이 하나의 seq
축에 이어 붙고 회차 지점에서 값이 바뀐다. seq 는 노선 전체에서 유일하므로
PK 는 `(route_id, seq)` 로 충분하다.

**`direction` 으로 조인하면 멀쩡한 후보가 죽는다.** 회차점은 종점이 아니라
노선 한복판이다 — 기점에서 가장 먼 정류장이 seq 67(18.4km)이고 seq 135 는
기점으로 되돌아온다. 그래서 강남역9번출구(seq 66)에서 타면 진흥아파트,
서초푸르지오써밋, 신논현역을 지나 네 정거장 뒤 강남역1번출구(seq 70)에
내린다. 행선판만 바뀔 뿐 같은 차량이다.

방향 문제는 **seq 비교가 이미 해결한다.** 도로 양쪽 정류장은 서로 다른
`stop_id` 에 서로 다른 `seq` 를 가지므로, 반대편에서 타면 seq 가 감소해
자동으로 탈락한다. 그래서 §9.1 질의의 조건은 `rs2.seq > rs1.seq` 하나뿐이다.
`test_crossing_the_turnaround_is_allowed` 와
`test_turnaround_point_is_mid_route_not_the_terminal` 이 이 전제를 고정한다.

**2. 도착정보 응답의 `<stId>` 는 조회한 정류장이 아니다.**
`arsId=22339`(강남역8번출구) 하나로 조회했는데 경기 광역버스 두 건은
`stId=221000178`(구리역7번출구)을 달고 온다. 그대로 쓰면 도착정보가 엉뚱한
정류장에 붙어 `route_stop` 조인이 **조용히** 어긋난다. 그래서
`parse_arrivals(xml, stop_id=...)` 로 호출자가 아는 ID 를 덮어쓴다.

**3. 미정차 정류소는 `arsId=0` 만으로 못 거른다.**
실물 23건 중 `arsId=0` 이 2건, 정상 `arsId`(78262)를 달고 이름에만
`(미정차)` 가 붙은 것이 1건이었다. 두 조건을 모두 본다.

---

## 알려진 함정

1. **정류장 방향 구분** — 같은 이름의 정류장이 도로 양쪽에 있다. `direction` 과 `seq` 로
   반드시 구분한다. 놓치면 반대 방향 버스를 추천하는 치명적 버그가 난다.
2. **Encoding / Decoding 키** — httpx 가 `params=` 를 자동 인코딩하므로 Encoding 키를
   넣으면 이중 인코딩되어 인증 실패한다. `scripts/smoke.py` 로 확정한다.
3. **호출 한도** — 개발계정은 보통 일 1,000건. Phase 1 수집 배치가 이 한도를 그냥
   태운다. 그래서 `collect_progress` 테이블로 중단 지점부터 재개할 수 있게 했다.
4. **HTTPS 필수** — iOS Safari 는 비보안 컨텍스트에서 `navigator.geolocation` 을
   차단한다. 폰에서 IP로 접속하면 즉시 막힌다. Phase 6 전에 확인할 것.
5. **`.env` 커밋 금지** — 저장소에는 `.env.example` 만 둔다.

---

## 구조

```
nowbus/
├── config.py      설정·튜닝 파라미터
├── models.py      코어 dataclass
├── db/            SQLite 스키마와 정적 조회
├── providers/     실시간 도착 (지역별 구현체 교체 가능)
├── collectors/    정적 데이터 수집 배치
├── core/          ★ 인터페이스에 무지한 순수 로직
├── web/           FastAPI
└── static/        Vanilla JS PWA
```

핵심 원칙은 **Planner 가 인터페이스에 무지하다**는 것. FastAPI든 CLI든 `plan_now()`
하나만 호출한다. 결과물 형태가 또 바뀌어도 코어는 그대로 재사용된다.
