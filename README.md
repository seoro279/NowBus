# NowBus

> **최종 갱신: 2026-09-08** · Phase 0·2 완료 / Phase 3 은 `planner.py` 만 남음 · 테스트 62개 통과

현재 위치에서 지금 출발했을 때, **실제로 걸어서 닿을 수 있는** 정류장·버스 조합만
추려서 목적지 도착이 가장 빠른 순으로 알려주는 개인용 도구.

기존 지도앱은 도보 6분 거리 정류장에 3분 뒤 오는 버스를 "3분 후 도착"이라고 그대로
표시한다. 못 타는 버스다. NowBus는 **catchability(탑승 가능성)** 를 1급 개념으로
올려서, 탈 수 없는 후보를 먼저 걸러낸다.

설계 문서는 `docs/` 참조.

---

## 현재 상태

| Phase | 내용 | 상태 |
|---|---|---|
| 0 | 스캐폴딩 + API 스모크 테스트 | ✅ 완료 |
| 1 | 정적 데이터 구축 (`route_stop`) | 🚧 `schema.sql` 만. **수집기 남음** |
| 2 | 오프라인 코어 (도보·직통조합) | ✅ 완료 |
| 3 | 실시간 결합 (Provider·판정·랭킹) | 🚧 `planner.py` 만 남음 |
| 4 | 서버 API | ⬜ |
| 5 | 모바일 웹 UI (PWA) | ⬜ |
| 6 | 배포 & 실기기 검증 | ⬜ |
| 7 | 실사용 검증 & 튜닝 | ⬜ |

### 지금 막힌 지점

**서울 전체 노선 ID 목록을 얻는 방법이 확인되지 않았다.** `getStaionByRoute` 는
`busRouteId` 를 알아야 부르는데, `getBusRouteList` 는 검색어를 요구한다. 일 1,000건
한도라 전수 조사도 부담이다.

집·회사 근처 정류장을 지나는 노선만 부분 수집하는 쪽이 실사용까지 빠르다
(설계서 §12 대응). 서울 열린데이터광장 노선 목록의 ID 체계가 `busRouteId`
(146번 = `100100025`)와 일치하는지 확인 중.

### 동작하는 것 / 아닌 것

알고리즘 7단계(설계서 §6.1) 중 6개가 구현됐다. 다만 **DB가 비어 있어 실제 요청은
아직 빈 결과를 낸다.**

| 단계 | 구현 | 상태 |
|---|---|---|
| 1. 주변 정류장 | `repo.stops_within()` | ✅ |
| 2. 도보 시간 | `core/walking.py` | ✅ |
| 3. 직통 조합 | `repo.find_direct_combos()` | ✅ |
| 4. 실시간 도착 | `providers/seoul.py` | ✅ |
| 5. 탑승 가능성 판정 | `core/catchability.py` | ✅ |
| 6. 총 소요시간 | `core/planner.py` | ❌ |
| 7. 랭킹·다양성 | `core/ranking.py` | ✅ |

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

Windows `cmd` 기준:

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install httpx
copy .env.example .env
notepad .env
```

### 3. 테스트

```bash
uv sync                # 또는 pip install -e '.[dev]'
uv run pytest          # 62개
uv run ruff check .
```

전부 오프라인이다. 실시간 API 호출 없이 저장된 픽스처로 돈다.

### 4. 스모크 테스트 (선택 — 이미 1회 완료)

```bash
python scripts/smoke.py
```

API 스펙이 바뀐 것 같을 때 다시 돌린다. 인증키 방식과 응답 필드명을 실물로
확인하고 `tests/fixtures/` 를 갱신한다. `.env` 가 없으면 키를 물어보며,
입력값은 화면에 찍히지 않는다.

> ⚠️ **원격 개발 컨테이너에서는 실행되지 않는다.** `ws.bus.go.kr` 과
> `apis.data.go.kr` 이 이그레스 정책에 403으로 막혀 있다. 로컬 PC에서 돌릴 것.

---

## API 실측 결과

추측이 아니라 `tests/fixtures/` 의 실제 응답에서 확인한 것들이다.
스펙이 바뀌면 픽스처에 물린 테스트가 먼저 깨진다.

| 항목 | 실측 |
|---|---|
| 인증키 | **Decoding** 방식으로 호출 (`httpx params=` 에 그대로) |
| `tmX` / `tmY` | 경도 / 위도 (이름과 달리 TM 좌표가 아니다) |
| `traTime1` | **초 단위**. `arrmsg1` 은 사람용 문자열이므로 파싱하지 않는다 |
| `term` | 배차간격(분). 1·2차가 모두 MISS 일 때 3차 추정에 쓴다 |
| `congestion1` | 1~4. **0 은 미측정**이지 '한산함'이 아니다 |
| `seq` | 노선 전체에서 유일. 그래서 PK 는 `(route_id, seq)` 로 충분 |
| stop_id | `getStaionByRoute` 의 `<station>` = `getStationByPos` 의 `<stationId>` |

### 함정 세 개 — 전부 실물에서 확인됨

**1. `direction` 은 상행/하행 코드가 아니라 버스 앞 행선판이다. 조인에 쓰면 안 된다.**

146번은 seq 1~68 이 `"강남역"`, 69~135 가 `"상계주공7단지"`. 왕복이 하나의 seq
축에 이어 붙고 회차 지점에서 값이 바뀐다.

**여기서 조인하면 멀쩡한 후보가 죽는다.** 회차점은 종점이 아니라 노선 한복판이다 —
기점에서 가장 먼 정류장이 seq 67(18.4km)이고 seq 135 는 기점으로 되돌아온다.
그래서 강남역9번출구(seq 66)에서 타면 진흥아파트, 서초푸르지오써밋, 신논현역을
지나 네 정거장 뒤 강남역1번출구(seq 70)에 내린다. 행선판만 바뀔 뿐 같은 차량이다.

방향 문제는 **seq 비교가 이미 해결한다.** 도로 양쪽 정류장은 서로 다른 `stop_id` 에
서로 다른 `seq` 를 가지므로, 반대편에서 타면 seq 가 감소해 자동으로 탈락한다.
그래서 §9.1 질의의 조건은 `rs2.seq > rs1.seq` 하나뿐이다.
`test_crossing_the_turnaround_is_allowed` 와
`test_turnaround_point_is_mid_route_not_the_terminal` 이 이 전제를 고정한다.

**2. 도착정보 응답의 `<stId>` 는 조회한 정류장이 아니다.**

`arsId=22339`(강남역8번출구) 하나로 조회했는데 경기 광역버스 두 건은
`stId=221000178`(구리역7번출구)을 달고 온다. 그대로 쓰면 도착정보가 엉뚱한
정류장에 붙어 `route_stop` 조인이 **조용히** 어긋난다. 에러가 나지 않고 "가끔
이상한 추천"으로만 나타나므로 원인 추적이 어렵다. 그래서
`parse_arrivals(xml, stop_id=...)` 로 호출자가 아는 ID 를 덮어쓴다.

**3. 미정차 정류소는 `arsId=0` 만으로 못 거른다.**

실물 23건 중 `arsId=0` 이 2건, 정상 `arsId`(78262)를 달고 이름에만 `(미정차)` 가
붙은 것이 1건이었다. 두 조건을 모두 본다.

---

## 설계 판단 기록

계획서와 다르게 간 것들. 이유가 남아 있어야 나중에 되돌리지 않는다.

**병렬 조회에 `TaskGroup` 대신 `gather(return_exceptions=True)`**
TaskGroup 은 한 태스크가 죽으면 형제를 취소한다. 계획서가 요구한 '부분 실패 허용'
(정류장 8곳 중 1곳이 타임아웃 나도 나머지로 결과 생성)이 성립하지 않는다.

**다양성 보정의 상한은 개수 제한이 없는 예외를 갖는다**
계획서 §10-4: "`max_per_stop` 은 동점에 가까운 후보들 사이의 타이브레이커여야 하며,
score 차이가 크면 순수 정렬을 우선한다." 그래서 상한에 걸린 후보라도 다른 정류장의
최선 대안보다 5분 이상 좋으면 통과시킨다. 개수는 제한하지 않는다.

`max_per_stop` 은 '비슷할 때 섞어라', `min_distinct_stops` 는 '항상 대안은 남겨라'.
역할이 다르다. 상한으로 대안을 보장하려 들면 더 빠른 버스를 숨기게 된다.

**`core/candidates.py` 폐기**
직통 조합 전개가 SQL 자기조인 한 방이라 별도 모듈이 될 게 없었다.
`db/repo.py` 의 `find_direct_combos()` 로 들어갔다.

**`collect_progress` 테이블 추가**
설계서 §9 스키마에 없던 것. 수집 배치가 일일 호출 한도에 걸려 중단되는 것은
예외가 아니라 기본값이라, 재개 지점을 DB에 남긴다.

---

## 운영상 주의

1. **Encoding / Decoding 키** — httpx 가 `params=` 를 자동 인코딩하므로 Encoding 키를
   넣으면 이중 인코딩되어 인증 실패한다. `%` 기호가 없는 쪽이 Decoding 이다.
2. **호출 한도** — 개발계정은 보통 일 1,000건. Phase 1 수집 배치가 이 한도를 그냥
   태운다. `collect_progress` 로 중단 지점부터 재개한다.
3. **HTTPS 필수** — iOS Safari 는 비보안 컨텍스트에서 `navigator.geolocation` 을
   차단한다. 폰에서 IP로 접속하면 즉시 막힌다. Phase 6 전에 확인할 것.
4. **`.env` 커밋 금지** — 저장소에는 `.env.example` 만 둔다.

---

## 구조

```
nowbus/
├── config.py           [x] 설정·튜닝 파라미터
├── models.py           [x] 코어 dataclass
├── cache.py            [x] TTL 캐시
├── schemas.py          [ ] pydantic 응답 스키마
├── db/
│   ├── schema.sql      [x]
│   └── repo.py         [x] 정적 조회 + 직통 조합 전개
├── providers/
│   ├── base.py         [x] ABC + 병렬 + 부분 실패 허용
│   └── seoul.py        [x] 서울 TOPIS 파서
├── collectors/         [ ] 정적 데이터 수집 배치
├── core/                   ★ 인터페이스에 무지한 순수 로직
│   ├── walking.py      [x]
│   ├── catchability.py [x]
│   ├── ranking.py      [x]
│   └── planner.py      [ ] 오케스트레이션
├── web/                [ ] FastAPI
└── static/             [ ] Vanilla JS PWA

scripts/smoke.py        [x] API 진단 도구
tests/                  [x] 62개. 픽스처 기반이라 오프라인
```

핵심 원칙은 **Planner 가 인터페이스에 무지하다**는 것. FastAPI든 CLI든 `plan_now()`
하나만 호출한다. 결과물 형태가 또 바뀌어도 코어는 그대로 재사용된다.
