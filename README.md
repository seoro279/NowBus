# NowBus

> **최종 갱신: 2026-09-19** · Phase 0~6 완료, 7 진행 중 · 테스트 136개 통과
> 서울 전역 데이터 적재 완료 — 노선 718개 / 정류장 12,897개 / 경유 41,688행

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
| 1 | 정적 데이터 구축 (`route_stop`) | ✅ 완료 (xlsx 적재, API 호출 0회) |
| 2 | 오프라인 코어 (도보·직통조합) | ✅ 완료 |
| 3 | 실시간 결합 (Provider·판정·랭킹) | ✅ 완료 |
| 4 | 서버 API | ✅ 완료 |
| 5 | 모바일 웹 UI (PWA) | ✅ 완료 (데스크톱 모바일 뷰 검증) |
| 6 | 배포 & 실기기 검증 | ✅ 완료 (D-9: 집 맥 + Tailscale Funnel) |
| 7 | 실사용 검증 & 튜닝 | 🔄 진행 중 (1주 실사용 → 아래 두 건 반영) |

### 실사용 1주 뒤 반영한 것

| 불만 | 반영 |
|---|---|
| 좌표를 몰라서 장소를 추가하기 어렵다 | **장소 이름·주소 검색** (`GET /api/geocode`, `nowbus search`, 앱의 '장소 추가' 화면) |
| 1~2분 뒤 도착하는 버스가 목록에 없다. 뛰면 타는데 | **RUN 등급** — 걸어선 놓치지만 뛰면 잡히는 버스를 별도 칸에 따로 보여준다 |

### 동작하는 것

알고리즘 7단계(설계서 §6.1)가 전부 이어졌고, **서울 전역 데이터로 검증됐다.**

```
강남역 → 서울시청   실시간 API 8회, 총 162ms

1. [SAFE ] 지하철2호선강남역  도보 7분
     새벽A741번  9분 후 도착   여유 +2분
     → 광화문역 하차, 도보 8분
     총 28분
...
서로 다른 승차 정류장 3곳
```

실시간 부분은 목 Provider 로 돌린 것이다. 실제 API 는 개발 컨테이너에서 막혀 있어
로컬에서 확인해야 한다 (Phase 4 이후).

| 단계 | 구현 | 상태 |
|---|---|---|
| 1. 주변 정류장 | `repo.stops_within()` | ✅ |
| 2. 도보 시간 | `core/walking.py` | ✅ |
| 3. 직통 조합 | `repo.find_direct_combos()` | ✅ |
| 4. 실시간 도착 | `providers/seoul.py` | ✅ |
| 5. 탑승 가능성 판정 | `core/catchability.py` | ✅ (SAFE/TIGHT/**RUN**/MISS) |
| 6. 총 소요시간 | `core/planner.py` | ✅ |
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

**장소 검색을 제대로 쓰려면 카카오 REST 키를 하나 더 넣는다** (선택).
[developers.kakao.com](https://developers.kakao.com/) → 애플리케이션 추가 →
앱 키의 **REST API 키** 를 `NOWBUS_KAKAO_REST_KEY` 에 넣는다. 그리고
**제품 설정에서 '카카오맵' 을 활성화해야 한다** - 키만 발급하면
`403 App(...) disabled OPEN_MAP_AND_LOCAL service.` 가 돌아온다. 없어도 장소 검색은
동작하지만 **DB 에 있는 정류장 이름만** 찾는다. 건물명·상호·도로명주소로 찾으려면
이 키가 필요하다.

> 키워드·주소 검색 **양쪽 모두 실물 응답으로 확인했다** (2026-09-19).
> 스펙이 의심되면 `python scripts/smoke_geocode.py "세종대로 110"` 을 로컬에서
> 돌린다 — 원본 응답을 `tests/fixtures/` 에 저장하고 파서에 통과시킨다.
> (개발 컨테이너에서는 `dapi.kakao.com` 이 막혀 있어 실행되지 않는다.)

Windows `cmd` 기준:

```cmd
python -m venv .venv
.venv\Scripts\activate
pip install httpx
copy .env.example .env
notepad .env
```

### 3. 정적 데이터 적재

[서울시 버스노선별 정류소정보](https://data.seoul.go.kr/dataList/OA-15067/S/1/datasetView.do)
xlsx 를 받아 `data/raw/` 에 두고:

```bash
python -m nowbus.collectors.build_db "data/raw/서울시버스노선별정류소정보(20260902).xlsx"
```

3초면 끝나고 **API 호출이 0회**다. 이 파일 하나에 노선별 경유 순서가 전부 들어
있어서, `getStaionByRoute` 를 노선 수(718개)만큼 부를 필요가 없다. 그렇게 했으면
일일 한도 1,000건을 수집만으로 태웠을 것이다.

### 4. 테스트

```bash
uv sync                # 또는 pip install -e '.[dev]'
uv run pytest          # 136개
uv run ruff check .
```

전부 오프라인이다. 실시간 API 호출 없이 저장된 픽스처로 돈다.

### 5. 실제로 조회해보기

장소는 **이름으로 찾는다.** 좌표를 직접 입력할 필요가 없다.

```bash
pip install -e .
nowbus search 서울시청
#   1. 서울특별시청  [kakao-keyword]
#      37.566535,126.977969   서울 중구 세종대로 110
#   2. 시청  [정류장]
#      37.565661,126.977380   버스 정류장 · 노선 24개

nowbus place add 회사 -q "세종대로 110"   # 검색 결과 중 번호를 고른다
nowbus place add 집                      # 이름을 그대로 검색어로 쓴다
nowbus plan 집 회사
```

좌표를 이미 안다면 그대로 넣어도 된다: `nowbus place add 집 37.6612 127.0585`,
`nowbus plan 37.6612,127.0585 회사`

> **정류장 좌표를 장소로 쓰지 말 것.** 검색 결과에 `[정류장]` 이 붙은 항목은
> 정류장 위치다. 그걸 출발지로 저장하면 도보 시간이 0 으로 잡혀 탑승 가능성
> 판정이 무의미해진다 - 이 프로그램의 존재 이유가 사라진다. CLI 도 앱도 이
> 경우 경고한다. 정류장밖에 안 나올 때는 지도에서 집 위치 좌표를 직접 주는 편이
> 정확하다.

튜닝 옵션: `--radius 800` `--top 8` `--m-safe 4`

정류장 이름만 보고 싶으면 `nowbus stops 상계주공` 이 그대로 남아 있다.

```
집 -> 회사   07:51 기준  (812ms)

  [지금 뛰면 잡을 수 있다]

1. [RUN  ] 상계주공7단지  뛰어서 2분 (걸으면 3분)
      146번  2분 후 도착   여유 +0분
      -> 강남역9번출구 하차, 도보 4분
      08:19 도착 예상 (총 28분)

  [걸어서 잡을 수 있다]

1. [SAFE ] 지하철2호선강남역  도보 7분
      741번  9분 후 도착   여유 +2분  (혼잡도 3)
      -> 광화문역 하차, 도보 8분
      08:26 도착 예상 (총 35분)
...
  서로 다른 승차 정류장 3곳
```

> 이 명령만 실제 API 를 부른다. 개발 컨테이너에서는 막혀 있으므로 로컬 PC 에서
> 실행할 것.

### 6. 폰에서 쓰기

```bash
nowbus serve                 # http://0.0.0.0:8000
```

같은 와이파이의 폰에서 `http://<PC-IP>:8000` 으로 열린다. 단 **위치는 안 잡힌다** —
iOS Safari 는 비보안 컨텍스트에서 `navigator.geolocation` 을 차단하고, 앱은 그
경우 "HTTPS 가 아니라 위치를 쓸 수 없어요" 를 띄운다. 즐겨찾기 출발지 폴백으로는
쓸 수 있다. 제대로 쓰려면 Phase 6 의 HTTPS 가 필요하다.

첫 실행에 토큰을 물어본다. `.env` 의 `NOWBUS_API_TOKEN` 값을 넣으면
localStorage 에 저장된다.

캐시가 의심스러우면 `?nosw=1` 로 열어 서비스 워커와 캐시를 지운다.

### 7. 서버 API 직접 호출

```bash
nowbus serve                 # http://0.0.0.0:8000
nowbus serve --reload        # 개발용
```

```bash
curl -G --data-urlencode "from=집" --data-urlencode "to=회사" \
     -H "X-Token: $NOWBUS_API_TOKEN" http://127.0.0.1:8000/api/plan
```

| 엔드포인트 | 용도 |
|---|---|
| `GET /api/plan?lat=&lon=&to=회사` | GPS 기반 |
| `GET /api/plan?from=집&to=회사` | 즐겨찾기 (GPS 실패 폴백) |
| `GET /api/geocode?q=서울시청&lat=&lon=` | 장소 이름·주소 → 좌표 [F-19] |
| `GET /api/places` · `POST /api/places` | 즐겨찾기 |
| `POST /api/feedback` | 실측 도보시간 [F-11] |

`/api/plan` 응답은 목록이 둘이다. `items` 는 걸어서 잡는 후보(최대 5개),
`sprint` 는 **걸어선 놓치지만 뛰면 잡히는** 후보(최대 3개)다. 같은 정류장·노선이
양쪽에 나오는 것이 정상이다 - '뛰면 2분 뒤 차, 안 뛰면 12분 뒤 차' 가 둘 다 사실이다.

인증은 `X-Token` 헤더 또는 `?token=` 쿼리. 후자는 curl 로 찔러볼 때만 쓴다.

> 한글 파라미터는 URL 인코딩해야 한다. `curl -G --data-urlencode` 를 쓰거나
> 좌표(`lat`/`lon`/`to_lat`/`to_lon`)로 넘길 것.

### 8. 스모크 테스트 (선택 — 이미 1회 완료)

```bash
python scripts/smoke.py
```

API 스펙이 바뀐 것 같을 때 다시 돌린다. 인증키 방식과 응답 필드명을 실물로
확인하고 `tests/fixtures/` 를 갱신한다. `.env` 가 없으면 키를 물어보며,
입력값은 화면에 찍히지 않는다.

> ⚠️ **원격 개발 컨테이너에서는 실행되지 않는다.** `ws.bus.go.kr` 과
> `apis.data.go.kr` 이 이그레스 정책에 403으로 막혀 있다. 로컬 PC에서 돌릴 것.

```bash
python scripts/smoke_geocode.py "강남역 스타벅스"
```

카카오 로컬 API 쪽 스모크. **장소 검색을 쓰기 전에 한 번은 돌려야 한다** - 파싱을
문서만 보고 썼기 때문이다. 실물 응답을 `tests/fixtures/` 에 저장하고 파서에
통과시켜 본다. `dapi.kakao.com` 도 개발 컨테이너에서는 막혀 있다.

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

**뛰는 후보(RUN)를 본 목록과 섞지 않고 따로 낸다**
점수 하나로 합치면 둘 중 하나가 반드시 사라진다. 뛰는 후보는 대기시간이 0 이라
총 소요시간이 압도적으로 짧아서 벌점 없이는 화면을 독점하고, 벌점을 얹으면 상위
5개에서 밀려 아예 안 보인다. 실사용에서 후자였다. 그래서 랭킹(§6.4.1)은 손대지
않고 `PlanSet(best, sprint)` 로 칸을 따로 줬다.

판정도 두 함수로 나눠 두었다. `pick_boardable()` 은 걷는 경우만, `pick_sprint()`
는 뛰는 경우만 본다. 하나로 합치면 '1분 뒤 차(뛰면 잡음)' 가 '9분 뒤 차(여유
있음)' 를 가려버린다 - 둘은 서로를 대체하지 못한다.

뛰는 후보에는 필터가 하나 붙는다. **걸어서 잡는 최선보다 총 소요시간이 짧지
않으면 보여주지 않는다.** 뛰어서 얻는 게 없으면 뛰라고 할 이유가 없다.

**장소 검색은 정류장 이름 + 카카오 두 공급원을 번갈아 담는다**
정류장 검색(`StopGeocoder`)은 키도 네트워크도 필요 없어 항상 켜 두고, 카카오는
키가 있을 때만 붙는다. 앞 공급원부터 순서대로 채우면 "강남" 같은 질의에서 정류장
이름만으로 목록이 차서 건물·상호 결과가 화면에 못 올라온다. 그래서 하나씩 번갈아
담는다.

카카오 키워드 검색에 `radius` 와 `sort=distance` 를 주지 않는다. 목적지를 찾는
검색인데 현재 위치 기준으로 자르거나 정렬하면 '내가 말한 그 지점'이 밀려난다.
거리는 응답과 무관하게 우리가 haversine 으로 계산해 붙인다.

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
5. **카카오는 키 발급만으로 안 된다** — 제품 설정에서 '카카오맵' 을 활성화해야
   한다. 안 켜면 `403 ... disabled OPEN_MAP_AND_LOCAL service.` 가 온다.
   장소 검색이 이상하면 먼저 `scripts/smoke_geocode.py` 를 돌려볼 것 — 응답
   본문과 조치를 같이 찍는다. 키가 없으면 정류장 이름만 검색되는 게 정상이다.

---

## 구조

```
nowbus/
├── config.py           [x] 설정·튜닝 파라미터
├── models.py           [x] 코어 dataclass
├── cache.py            [x] TTL 캐시
├── schemas.py          [x] API 계약 (pydantic)
├── db/
│   ├── schema.sql      [x]
│   └── repo.py         [x] 정적 조회 + 직통 조합 전개
├── providers/
│   ├── base.py         [x] ABC + 병렬 + 부분 실패 허용
│   ├── seoul.py        [x] 서울 TOPIS 파서
│   └── geocode.py      [x] 장소 검색 (정류장 이름 + 카카오 로컬)
├── collectors/         [x] xlsx → route_stop 적재
├── core/                   ★ 인터페이스에 무지한 순수 로직
│   ├── walking.py      [x] 도보·뛰는 시간
│   ├── catchability.py [x] SAFE/TIGHT/RUN/MISS
│   ├── ranking.py      [x]
│   └── planner.py      [x] 오케스트레이션
├── cli.py              [x] 개발·검증용 (typer)
├── web/                [x] FastAPI (app / deps / routes)
└── static/             [x] Vanilla JS PWA (빌드 없음)
    ├── index.html      [x] 화면 3개 (홈 / 결과 / 장소 검색)
    ├── style.css       [x] 다크모드 · safe-area · 56px 터치
    ├── app.js          [x] GPS 선획득 · 카드 · 신선도 · 폴백 · 장소 검색
    ├── sw.js           [x] 앱 셸 (network-first)
    └── icons/          [x] scripts/make_icons.py 로 생성

scripts/smoke.py        [x] 버스 API 진단 도구
scripts/smoke_geocode.py [x] 카카오 로컬 응답 확인 (로컬에서 1회 필수)
tests/                  [x] 136개. 픽스처 기반이라 오프라인
```

핵심 원칙은 **Planner 가 인터페이스에 무지하다**는 것. FastAPI든 CLI든 `plan_now()`
하나만 호출한다. 결과물 형태가 또 바뀌어도 코어는 그대로 재사용된다.
