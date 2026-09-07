"""서울시 TOPIS(ws.bus.go.kr) 구현.

필드명과 단위는 tests/fixtures/ 의 실제 응답에서 확인한 것이다. 추측이 아니다.
스펙이 바뀌면 픽스처 기반 테스트가 먼저 깨진다.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from nowbus.models import Arrival, RouteStopRow, Stop
from nowbus.providers.base import ArrivalProvider

BASE_URL = "http://ws.bus.go.kr/api/rest"

# 도착정보가 아니라 '지금은 차가 없다'는 뜻인 메시지들.
_NOT_RUNNING = ("운행종료", "출발대기", "정보없음", "차고지대기")


class SeoulApiError(RuntimeError):
    pass


def _text(node: ET.Element, tag: str) -> str:
    return (node.findtext(tag) or "").strip()


def _int_or_none(raw: str) -> int | None:
    """빈 값과 0 을 '정보 없음'으로 본다. 혼잡도 0 은 미측정을 뜻한다."""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    return v or None


def check_header(xml_text: str) -> None:
    """<headerCd> 가 0 이 아니면 에러로 올린다. 호출 한도 초과도 여기서 잡힌다."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise SeoulApiError(f"XML 파싱 실패: {e}") from e
    cd = root.findtext(".//headerCd")
    if cd is not None and cd.strip() != "0":
        raise SeoulApiError(f"headerCd={cd} headerMsg={root.findtext('.//headerMsg')}")


# ---------------------------------------------------------------- 실시간 도착
def parse_arrivals(xml_text: str, stop_id: str | None = None) -> list[Arrival]:
    """getStationByUid 응답 → Arrival 목록.

    itemList 1개가 노선 1개이고, 그 안에 1차/2차 차량 정보가 접미사 1/2 로 들어 있다.

    stop_id 를 반드시 넘길 것. 응답의 <stId> 는 조회한 정류장을 가리키지 않는다.
    실물에서 arsId=22339(강남역8번출구)로 조회했는데 경기 광역버스 두 건은
    stId=221000178(구리역7번출구)를 달고 왔다. 그걸 그대로 쓰면 도착정보가
    엉뚱한 정류장에 붙어 route_stop 조인이 조용히 어긋난다.
    """
    check_header(xml_text)
    root = ET.fromstring(xml_text)
    out: list[Arrival] = []
    for it in root.findall(".//itemList"):
        route_id = _text(it, "busRouteId")
        resolved = stop_id or _text(it, "stId")
        if not route_id or not resolved:
            continue
        for order in (1, 2):
            msg = _text(it, f"arrmsg{order}")
            if not msg or any(bad in msg for bad in _NOT_RUNNING):
                continue
            raw_sec = _text(it, f"traTime{order}")
            try:
                eta_min = int(raw_sec) / 60.0
            except ValueError:
                continue
            # isArrive=1 이면 지금 정류장에 서 있다.
            if _text(it, f"isArrive{order}") == "1":
                eta_min = 0.0
            out.append(
                Arrival(
                    route_id=route_id,
                    stop_id=resolved,
                    eta_min=eta_min,
                    order=order,
                    congestion=_int_or_none(_text(it, f"congestion{order}")),
                    is_last=_text(it, f"isLast{order}") == "1",
                )
            )
    return out


def parse_headways(xml_text: str) -> dict[str, float]:
    """route_id → 배차간격(분). 1·2차가 모두 MISS 일 때 3차를 추정하는 데 쓴다."""
    check_header(xml_text)
    root = ET.fromstring(xml_text)
    out: dict[str, float] = {}
    for it in root.findall(".//itemList"):
        route_id = _text(it, "busRouteId")
        term = _int_or_none(_text(it, "term"))
        if route_id and term:
            out[route_id] = float(term)
    return out


# ---------------------------------------------------------------- 정적 데이터
def parse_stations_by_pos(xml_text: str, *, skip_non_stopping: bool = True) -> list[Stop]:
    """getStationByPos 응답 → Stop 목록.

    미정차 정류소를 거른다. 거르지 않으면 탈 수 없는 정류장을 추천하게 되는데,
    그건 이 프로그램이 없애려는 문제 그 자체다.

    판별 조건이 둘인 이유: arsId=0 만 보면 놓친다. 실물 23건 중 arsId=0 인 것이
    2건, 정상 arsId(78262)를 달고 이름에만 '(미정차)'가 붙은 것이 1건이었다.
    """
    check_header(xml_text)
    root = ET.fromstring(xml_text)
    out: list[Stop] = []
    for it in root.findall(".//itemList"):
        ars = _text(it, "arsId")
        name = _text(it, "stationNm")
        if skip_non_stopping and (ars in ("", "0") or "미정차" in name):
            continue
        try:
            lat, lon = float(_text(it, "gpsY")), float(_text(it, "gpsX"))
        except ValueError:
            continue
        out.append(
            Stop(
                stop_id=_text(it, "stationId"),
                ars_id=ars,
                name=name,
                lat=lat,
                lon=lon,
            )
        )
    return out


def parse_route_stops(xml_text: str) -> list[RouteStopRow]:
    """getStaionByRoute 응답 → route_stop 행 목록. F-09 의 원천이다.

    stop_id 는 <station> 이다. getStationByPos 의 <stationId>, getStationByUid 의
    <stId> 와 같은 체계임을 실물로 대조 확인했다.
    """
    check_header(xml_text)
    root = ET.fromstring(xml_text)
    out: list[RouteStopRow] = []
    for it in root.findall(".//itemList"):
        stop_id = _text(it, "station")
        if not stop_id:
            continue
        try:
            seq = int(_text(it, "seq"))
            lat, lon = float(_text(it, "gpsY")), float(_text(it, "gpsX"))
        except ValueError:
            continue
        out.append(
            RouteStopRow(
                route_id=_text(it, "busRouteId"),
                route_name=_text(it, "busRouteNm") or _text(it, "busRouteAbrv"),
                stop_id=stop_id,
                ars_id=_text(it, "arsId") or None,
                stop_name=_text(it, "stationNm"),
                lat=lat,
                lon=lon,
                seq=seq,
                direction=_text(it, "direction") or None,
            )
        )
    return out


# ---------------------------------------------------------------- Provider
class SeoulProvider(ArrivalProvider):
    """스모크 테스트에서 decoding 방식이 확인되었으므로 params= 로 그냥 넘긴다."""

    def __init__(
        self,
        api_key: str,
        client: httpx.AsyncClient,
        *,
        cache_ttl_sec: float = 30.0,
    ) -> None:
        super().__init__(cache_ttl_sec)
        self._key = api_key
        self._client = client

    async def _get(self, path: str, params: dict) -> str:
        r = await self._client.get(f"{BASE_URL}/{path}", params={**params, "serviceKey": self._key})
        r.raise_for_status()
        return r.text

    async def _fetch(self, stop: Stop) -> list[Arrival]:
        # getStationByUid 는 stationId 가 아니라 arsId 를 받는다.
        if not stop.ars_id or stop.ars_id == "0":
            return []
        xml_text = await self._get("stationinfo/getStationByUid", {"arsId": stop.ars_id})
        return parse_arrivals(xml_text, stop_id=stop.stop_id)

    async def headways_at_stop(self, stop: Stop) -> dict[str, float]:
        if not stop.ars_id or stop.ars_id == "0":
            return {}
        xml_text = await self._get("stationinfo/getStationByUid", {"arsId": stop.ars_id})
        return parse_headways(xml_text)
