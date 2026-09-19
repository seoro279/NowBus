"""서울시 버스노선별 정류소정보 xlsx → route_stop.

서울 열린데이터광장 OA-15067 의 시트 다운로드를 그대로 읽는다. 이 파일 하나에
노선별 경유 순서가 전부 들어 있어서 **API 호출이 0회**다. getStaionByRoute 를
노선 수(718개)만큼 부르면 일일 한도 1,000건을 그것만으로 태운다.

컬럼: ROUTE_ID | 노선명 | 순번 | NODE_ID | ARS_ID | 정류소명 | X좌표 | Y좌표
    - NODE_ID 가 stop_id 다. 실시간 API 의 stationId/station 과 같은 체계임을
      146번으로 대조 확인했다.
    - X 가 경도, Y 가 위도. 뒤집으면 정류장이 서해에 찍힌다.
    - direction(행선지) 컬럼은 없다. route_stop.direction 은 NULL 로 남는다.
      직통 조합 판정은 seq 만 쓰므로 문제되지 않는다.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator

from nowbus.models import RouteStopRow

EXPECTED_HEADER = ["ROUTE_ID", "노선명", "순번", "NODE_ID", "ARS_ID", "정류소명", "X좌표", "Y좌표"]


class SheetFormatError(RuntimeError):
    pass


def parse_route_stop_xlsx(path: str | pathlib.Path) -> Iterator[RouteStopRow]:
    """행을 순서대로 흘려보낸다. 4만 행 규모라 스트리밍이 필요하진 않지만,
    read_only 모드가 메모리도 아끼고 로딩도 빠르다."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = ws.iter_rows(values_only=True)
        header = [str(c).strip() if c is not None else "" for c in next(rows)]
        if header[: len(EXPECTED_HEADER)] != EXPECTED_HEADER:
            raise SheetFormatError(
                f"컬럼이 예상과 다르다.\n  기대: {EXPECTED_HEADER}\n  실제: {header}"
            )
        for row in rows:
            if row is None or row[0] is None:
                continue
            route_id, route_name, seq, node_id, ars_id, stop_name, x, y = row[:8]
            try:
                yield RouteStopRow(
                    route_id=str(route_id).strip(),
                    route_name=str(route_name).strip(),
                    stop_id=str(node_id).strip(),
                    ars_id=str(ars_id).strip() or None,
                    stop_name=str(stop_name).strip(),
                    lat=float(y),
                    lon=float(x),
                    seq=int(seq),
                    direction=None,  # 이 파일엔 행선지가 없다
                )
            except (TypeError, ValueError):
                continue  # 깨진 행은 건너뛴다. 4만 행 중 하나 때문에 멈추지 않는다
    finally:
        wb.close()
