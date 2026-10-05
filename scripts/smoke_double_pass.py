#!/usr/bin/env python3
"""같은 정류장을 두 번 지나는 노선의 도착정보를 실물로 받는다 (인계 메모 §7).

5616번은 난곡우체국사거리(arsId 21218, stop_id 120000116)를 seq 14 와 87 에서 두 번
지난다. 이 정류장을 getStationByUid 로 조회해서 5616번 itemList 가 몇 건이고 각각
staOrd 가 무엇인지 본다. 원본은 tests/fixtures/getStationByUid_21218_5616.xml 로 저장한다.

NowBus 는 v5 부터 staOrd 로 pass 를 가린다. 결과에 따라 확인되는 것:
  - 두 건 (staOrd 14, 87)  → 각 pass 를 따로 계산한다. 지금 코드 그대로 맞다
  - 한 건                   → staOrd 가 가리키는 pass 로만 계산한다. 지금 코드 그대로 맞다.
                              다만 다른 pass 로 오는 차는 못 보므로, 어느 쪽이 오는지 기록할 것
  - staOrd 가 없음          → 예전 동작(짧은 쪽)으로 떨어진다. 이 경우 알려줄 것

개발 컨테이너에서는 ws.bus.go.kr 이 막혀 있으므로 맥에서 실행한다.

    python scripts/smoke_double_pass.py
    python scripts/smoke_double_pass.py 21218 5616

serviceKey 는 출력하지 않는다.
"""

from __future__ import annotations

import pathlib
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from smoke import _f, _load_key, call, header_of, looks_ok, save  # noqa: E402

ARS_ID = sys.argv[1] if len(sys.argv) > 1 else "21218"  # 난곡우체국사거리
ROUTE_NAME = sys.argv[2] if len(sys.argv) > 2 else "5616"


def main() -> None:
    key = _load_key()
    r = call("stationinfo/getStationByUid", {"arsId": ARS_ID}, key, "decoding")
    if not looks_ok(r):
        cd, msg = header_of(r.text)
        print(f"실패: http={r.status_code} headerCd={cd} headerMsg={msg}")
        print(r.text[:300])
        sys.exit(1)

    name = f"getStationByUid_{ARS_ID}_{ROUTE_NAME}"
    save(name, r.text)
    print(f"원본 저장: tests/fixtures/{name}.xml")
    print()

    items = [
        it
        for it in ET.fromstring(r.text).findall(".//itemList")
        if _f(it, "rtNm") == ROUTE_NAME or _f(it, "busRouteAbrv") == ROUTE_NAME
    ]
    print(f"arsId {ARS_ID} 에서 {ROUTE_NAME}번 itemList: {len(items)}건")
    for it in items:
        print(
            f"  staOrd={_f(it, 'staOrd') or '(없음)'}  stId={_f(it, 'stId')}  "
            f"다음={_f(it, 'nxtStn')}  행={_f(it, 'adirection')}"
        )
        for o in (1, 2):
            print(
                f"     {o}차 {_f(it, f'arrmsg{o}'):<20} traTime={_f(it, f'traTime{o}')}s "
                f"sectOrd={_f(it, f'sectOrd{o}')} 현재={_f(it, f'stationNm{o}')}"
            )
    if not items:
        print("  이 노선 도착정보가 없다 (운행 시간이 아니거나 노선명이 다르다).")
    print()
    print("이 출력을 그대로 대화에 붙여넣어 주세요 (키는 들어 있지 않습니다).")


if __name__ == "__main__":
    main()
