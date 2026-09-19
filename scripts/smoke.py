#!/usr/bin/env python3
"""Phase 0 스모크 테스트.

목적은 두 가지다.
  1. 발급받은 인증키가 Encoding / Decoding 중 어느 쪽으로 동작하는지 확정한다.
     (계획서 §10-11: "라이브러리마다 요구가 다르다. Phase 0에서 확정할 것")
  2. 세 서비스의 오퍼레이션명과 응답 필드를 실물로 확인해 tests/fixtures/ 에 저장한다.

원격 개발 컨테이너에서는 ws.bus.go.kr 이 이그레스 정책에 막히므로
로컬 PC에서 실행해야 한다.

    cp .env.example .env      # NOWBUS_SEOUL_API_KEY 채우기
    python scripts/smoke.py

serviceKey 는 절대 출력하지 않는다. 로그/스크린샷으로 새는 것을 막는다.
"""

from __future__ import annotations

import getpass
import os
import pathlib
import sys
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlencode

try:
    import httpx
except ModuleNotFoundError:
    sys.exit("httpx 가 없다.  pip install httpx  를 먼저 실행할 것.")

BASE = os.environ.get("NOWBUS_BUS_API_BASE", "http://ws.bus.go.kr/api/rest")
FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# 강남역 부근. 아는 좌표면 무엇이든 상관없다.
PROBE_LAT, PROBE_LON = 37.4979, 127.0276
PROBE_ROUTE_NAME = "146"

TIMEOUT = 10.0


def _read_env_file() -> dict[str, str]:
    """.env 를 읽는다. 메모장이 ANSI(cp949)나 BOM 으로 저장해도 깨지지 않게 한다."""
    path = pathlib.Path(".env")
    if not path.exists():
        return {}
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="ignore")

    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


def _load_key() -> str:
    key = os.environ.get("NOWBUS_SEOUL_API_KEY", "").strip()
    if not key:
        key = _read_env_file().get("NOWBUS_SEOUL_API_KEY", "")
    if not key:
        # .env 설정이 꼬였을 때를 위한 탈출구. 입력값은 화면에 찍히지 않는다.
        print("NOWBUS_SEOUL_API_KEY 를 .env 에서 찾지 못했다.")
        print("아래에 붙여넣으면 이번 실행에만 쓴다 (저장하지 않는다).")
        try:
            key = getpass.getpass("일반 인증키(Decoding): ").strip()
        except (EOFError, KeyboardInterrupt):
            key = ""
    if not key:
        sys.exit("인증키가 없다. .env 의 NOWBUS_SEOUL_API_KEY 를 채우고 다시 실행할 것.")
    return key


def call(path: str, params: dict, key: str, mode: str) -> httpx.Response:
    """mode='decoding': httpx 가 키를 인코딩한다. 'encoding': 키를 그대로 URL 에 박는다."""
    url = f"{BASE}/{path}"
    if mode == "decoding":
        return httpx.get(url, params={**params, "serviceKey": key}, timeout=TIMEOUT)
    qs = urlencode({k: str(v) for k, v in params.items()}, quote_via=quote)
    return httpx.get(f"{url}?{qs}&serviceKey={key}", timeout=TIMEOUT)


def _f(node: ET.Element, tag: str) -> str:
    return (node.findtext(tag) or "").strip()


def header_of(text: str) -> tuple[str | None, str | None]:
    """ws.bus.go.kr 응답의 <headerCd>/<headerMsg> 를 뽑는다. 실패하면 (None, None)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None, None
    cd = root.find(".//headerCd")
    msg = root.find(".//headerMsg")
    return (cd.text if cd is not None else None, msg.text if msg is not None else None)


def looks_ok(resp: httpx.Response) -> bool:
    if resp.status_code != 200:
        return False
    body = resp.text
    bad = (
        "SERVICE_KEY_IS_NOT_REGISTERED",
        "등록되지 않은",
        "SERVICE ERROR",
        "LIMITED_NUMBER_OF_SERVICE_REQUESTS",
        "<OpenAPI_ServiceResponse>",
    )
    if any(b in body for b in bad):
        return False
    cd, _ = header_of(body)
    # headerCd 0 = 정상. 코드 체계가 확실치 않으므로 0 이 아니면 본문을 눈으로 본다.
    return cd is None or cd == "0"


def save(name: str, text: str) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{name}.xml").write_text(text, encoding="utf-8")


def show(text: str, n: int = 500) -> str:
    return text[:n].replace("\n", " ")


# ---------------------------------------------------------------- step 1
def resolve_key_mode(key: str) -> str | None:
    """정류소 조회를 두 방식으로 때려보고 동작하는 쪽을 고른다."""
    print("=" * 72)
    print("STEP 1  인증키 모드 확정 (Encoding vs Decoding)")
    print("=" * 72)
    params = {"tmX": PROBE_LON, "tmY": PROBE_LAT, "radius": 300}
    winner = None
    for mode in ("decoding", "encoding"):
        try:
            r = call("stationinfo/getStationByPos", params, key, mode)
        except httpx.HTTPError as e:
            print(f"  {mode:<9} 전송 실패: {type(e).__name__}: {e}")
            continue
        cd, msg = header_of(r.text)
        ok = looks_ok(r)
        print(
            f"  {mode:<9} http={r.status_code} headerCd={cd} headerMsg={msg} -> "
            f"{'OK' if ok else 'FAIL'}"
        )
        if not ok:
            print(f"            {show(r.text, 240)}")
        elif winner is None:
            winner = mode
            save("getStationByPos", r.text)
    print()
    if winner:
        print(f"  ==> 인증키는 '{winner}' 방식으로 동작한다.")
        if winner == "decoding":
            print("      httpx params= 로 그냥 넘기면 된다. Provider 구현에 그대로 반영.")
        else:
            print("      키를 URL 에 직접 박아야 한다. params= 로 넘기면 이중 인코딩된다.")
    else:
        print("  ==> 둘 다 실패. 아래를 확인할 것:")
        print("      - 세 서비스 모두 '활용신청' 승인이 났는가 (키가 있어도 미신청이면 막힌다)")
        print("      - 승인 직후면 반영까지 시간이 걸릴 수 있다")
        print("      - 마이페이지의 Encoding/Decoding 키를 서로 바꿔 넣어봤는가")
    return winner


# ---------------------------------------------------------------- step 2
def probe_services(key: str, mode: str) -> None:
    print()
    print("=" * 72)
    print("STEP 2  세 서비스 오퍼레이션 확인 + 픽스처 저장")
    print("=" * 72)

    ars_id = None
    route_id = None

    # 2-1. 정류소정보조회: 좌표 -> 근접 정류소
    r = call(
        "stationinfo/getStationByPos",
        {"tmX": PROBE_LON, "tmY": PROBE_LAT, "radius": 300},
        key,
        mode,
    )
    if looks_ok(r):
        items = ET.fromstring(r.text).findall(".//itemList")
        print(f"  [정류소정보] getStationByPos      정류소 {len(items)}개")
        for it in items[:3]:
            print(
                f"      {_f(it, 'stationNm')}  arsId={_f(it, 'arsId')} "
                f"({_f(it, 'gpsY')}, {_f(it, 'gpsX')}) dist={_f(it, 'dist')}m"
            )
            ars_id = ars_id or _f(it, "arsId")
        print("      ! tmX/tmY 에 경도/위도를 넣었을 때 결과가 맞는지 확인할 것.")
    else:
        print(f"  [정류소정보] getStationByPos      실패: {show(r.text, 200)}")

    # 2-2. 버스도착정보조회: 정류소 -> 그 정류소의 전 노선 도착예정
    if ars_id:
        r = call("stationinfo/getStationByUid", {"arsId": ars_id}, key, mode)
        if looks_ok(r):
            save("getStationByUid", r.text)
            items = ET.fromstring(r.text).findall(".//itemList")
            print(f"  [버스도착정보] getStationByUid   arsId={ars_id} 도착 {len(items)}건")
            for it in items[:3]:
                print(
                    f"      {_f(it, 'rtNm'):>6}  stId={_f(it, 'stId')}  "
                    f"1차={_f(it, 'arrmsg1')} / 2차={_f(it, 'arrmsg2')}"
                )
                print(
                    f"              traTime1={_f(it, 'traTime1')}s "
                    f"congestion1={_f(it, 'congestion1')} "
                    f"isLast1={_f(it, 'isLast1')} term={_f(it, 'term')}min"
                )
                route_id = route_id or _f(it, "busRouteId")
            print("      ! stId 는 조회한 정류장을 가리키지 않을 수 있다 (광역버스).")
        else:
            print(f"  [버스도착정보] getStationByUid   실패: {show(r.text, 200)}")

    # 2-3. 노선정보조회: 노선명 -> route_id -> 경유 정류장 순서 (F-09 의 심장)
    r = call("busRouteInfo/getBusRouteList", {"strSrch": PROBE_ROUTE_NAME}, key, mode)
    if looks_ok(r):
        items = ET.fromstring(r.text).findall(".//itemList")
        print(f"  [노선정보] getBusRouteList        '{PROBE_ROUTE_NAME}' 검색 {len(items)}건")
        if items:
            route_id = _f(items[0], "busRouteId")
            print(f"      {_f(items[0], 'busRouteNm')} busRouteId={route_id}")
    else:
        print(f"  [노선정보] getBusRouteList        실패: {show(r.text, 200)}")

    if route_id:
        # 오퍼레이션명 오타('Staion')가 실제 스펙이다. 혹시 몰라 둘 다 시도한다.
        for op in ("busRouteInfo/getStaionByRoute", "busRouteInfo/getStationByRoute"):
            r = call(op, {"busRouteId": route_id}, key, mode)
            if not looks_ok(r):
                print(f"  [노선정보] {op.split('/')[1]:<20} 실패: {show(r.text, 160)}")
                continue
            save("getStaionByRoute", r.text)
            items = ET.fromstring(r.text).findall(".//itemList")
            print(
                f"  [노선정보] {op.split('/')[1]:<20} "
                f"경유 정류장 {len(items)}개  <- route_stop 원천"
            )
            for it in items[:3]:
                print(
                    f"      seq={_f(it, 'seq')} {_f(it, 'stationNm')} "
                    f"station={_f(it, 'station')} dir={_f(it, 'direction')}"
                )
            print("      ! seq 와 direction 이 채워지는지가 관건이다.")
            break


def main() -> None:
    key = _load_key()
    print(f"BASE={BASE}  key length={len(key)} (값은 출력하지 않는다)\n")
    mode = resolve_key_mode(key)
    if not mode:
        sys.exit(1)
    probe_services(key, mode)
    print()
    print("=" * 72)
    print(f"저장된 픽스처: {FIXTURES}")
    print(f".env 에 NOWBUS_SEOUL_KEY_MODE={mode} 를 기록해 두면 Provider 가 참조한다.")
    print("=" * 72)


if __name__ == "__main__":
    main()
