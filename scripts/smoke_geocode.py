#!/usr/bin/env python3
"""카카오 로컬(장소 검색) 스모크 테스트.

**이걸 한 번 돌려야 장소 검색을 신뢰할 수 있다.**

nowbus/providers/geocode.py 의 카카오 파싱은 개발자 문서를 보고 쓴 것이고,
실제 응답으로 확인하지 못했다 - 개발 컨테이너에서 dapi.kakao.com 이 조직
이그레스 정책에 막혀 있다. 이 스크립트가 실물 응답을 받아 파서에 통과시키고
원본을 tests/fixtures/ 에 저장한다. 필드명이 다르면 저장된 픽스처를 보고
_parse_kakao() 를 고칠 것. 추측으로 고치지 말 것.

    # 키 발급: developers.kakao.com → 애플리케이션 → 앱 키 → REST API 키
    # .env 에 NOWBUS_KAKAO_REST_KEY=... 를 넣거나, 물어볼 때 붙여넣으면 된다
    python scripts/smoke_geocode.py "강남역 스타벅스"

REST 키는 절대 출력하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

try:
    import httpx
except ModuleNotFoundError:
    sys.exit("httpx 가 없다.  pip install -e '.[dev]'  를 먼저 실행할 것.")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from nowbus.config import Settings  # noqa: E402
from nowbus.providers.geocode import KAKAO_BASE, _parse_kakao  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _key() -> str:
    key = Settings().kakao_rest_key
    if key:
        return key
    import getpass

    key = getpass.getpass("카카오 REST API 키 (화면에 안 보인다): ").strip()
    if not key:
        sys.exit("키가 없으면 확인할 수 없다.")
    return key


async def probe(key: str, query: str) -> int:
    headers = {"Authorization": f"KakaoAK {key}"}
    failures = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for path, source in (("keyword.json", "kakao-keyword"), ("address.json", "kakao-address")):
            print(f"\n=== {path}  query={query!r}")
            try:
                r = await client.get(
                    f"{KAKAO_BASE}/{path}", params={"query": query, "size": 5}, headers=headers
                )
            except httpx.HTTPError as e:
                print(f"  ! 요청 실패: {type(e).__name__}: {e}")
                failures += 1
                continue

            print(f"  HTTP {r.status_code}")
            if r.status_code == 401:
                print("  ! 키가 거부됐다. REST API 키인지 확인할 것 (JavaScript 키가 아니다).")
                failures += 1
                continue
            if r.status_code != 200:
                print(f"  ! 본문: {r.text[:300]}")
                failures += 1
                continue

            body = r.json()
            out = FIXTURES / f"{path.replace('.json', '')}_kakao.json"
            out.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  원본 저장: {out}")

            docs = body.get("documents") or []
            print(f"  documents {len(docs)}건")
            if docs:
                print(f"  키 목록: {sorted(docs[0].keys())}")

            hits = _parse_kakao(body, source)
            print(f"  파서 통과 {len(hits)}건")
            for h in hits[:5]:
                print(f"    - {h.name}  {h.lat:.6f},{h.lon:.6f}  {h.address or '(주소 없음)'}")
            if docs and not hits:
                print("  ! 응답은 왔는데 파서가 0건이다. 필드명이 문서와 다르다 - 위 키 목록과")
                print("    저장된 픽스처를 보고 _parse_kakao() 를 고칠 것.")
                failures += 1
    return failures


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "서울시청"
    failures = asyncio.run(probe(_key(), query))
    if failures:
        sys.exit(f"\n{failures}건 실패. 위 메시지를 보고 파서나 키를 고칠 것.")
    print("\n전부 통과. 장소 검색을 그대로 써도 된다.")


if __name__ == "__main__":
    main()
