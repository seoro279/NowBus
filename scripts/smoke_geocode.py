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
    key = Settings().kakao_rest_key.strip()
    if not key:
        import getpass

        key = getpass.getpass("카카오 REST API 키 (화면에 안 보인다): ").strip()
    if not key:
        sys.exit("키가 없으면 확인할 수 없다.")

    # 키를 그대로 헤더에 넣기 전에 여기서 걸러낸다. ASCII 가 아니면 httpx 가
    # 헤더를 만들다 UnicodeEncodeError 로 죽어서, 원인이 '키가 이상하다'라는 게
    # 스택 트레이스에 묻힌다. 실제로 안내문의 '발급받은키' 를 그대로 넣은 사례가 있었다.
    if not key.isascii():
        sys.exit(
            f".env 의 NOWBUS_KAKAO_REST_KEY 에 한글이 들어 있다: {_fingerprint(key)}\n"
            "  안내문의 자리표시자가 아니라 카카오에서 받은 실제 키(영문+숫자)를 넣을 것."
        )
    if " " in key or "\t" in key:
        sys.exit("키에 공백이 섞여 있다. 따옴표 없이 값만 붙여넣을 것.")
    print(f"키 확인: {_fingerprint(key)}  (값은 출력하지 않는다)")
    return key


def _fingerprint(key: str) -> str:
    """키를 노출하지 않고 '무엇을 넣었는지'만 알려준다.

    REST API 키는 32자 소문자 16진수다. 대시보드의 것과 모양이 다르면 여기서 보인다.
    """
    shape = (
        "16진수 32자"
        if len(key) == 32 and all(c in "0123456789abcdef" for c in key)
        else "형식 불일치"
    )
    return f"{len(key)}자, {shape}"


def _hint(status: int, body: str) -> list[str]:
    """카카오가 본문에 적어 준 이유를 사람이 할 행동으로 옮긴다.

    실물에서 받은 것: 403 + "App(Nowbus) disabled OPEN_MAP_AND_LOCAL service."
    키는 정상인데 앱에서 지도/로컬 서비스가 꺼져 있던 경우다. 이 메시지를 모르면
    키를 계속 다시 발급하게 된다 - 키 문제가 아니었다.
    """
    if "OPEN_MAP_AND_LOCAL" in body:
        return [
            "키는 정상이다. 앱에서 지도/로컬 서비스가 꺼져 있다.",
            "카카오 개발자 > 내 애플리케이션 > 제품 설정 > '카카오맵' 활성화 ON",
            "(활성화가 웹 플랫폼 등록을 요구하면 사이트 도메인에 http://localhost:8000 을 넣는다)",
        ]
    if status == 401:
        return [
            "401 은 키 거부다. 확인 순서:",
            "1) 카카오 개발자 > 내 애플리케이션 > 앱 키 > 'REST API 키'",
            "   (JavaScript / 네이티브 앱 / Admin 키가 아니다)",
            "2) .env 에 NOWBUS_KAKAO_REST_KEY 줄이 두 개면 아래 줄이 이긴다",
        ]
    if status == 403:
        return ["403 은 권한 거부다. 위 message 가 어느 서비스를 막았는지 말해 준다."]
    if status == 429:
        return ["429 는 호출 한도 초과다. 잠시 뒤 다시 돌릴 것."]
    return []


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
            if r.status_code != 200:
                # 본문을 반드시 찍는다. 카카오는 여기에 거부 이유를 적어 주고,
                # 그게 없으면 'REST 키가 아니다' 와 '이 앱에 권한이 없다' 를
                # 구분할 수 없다. 본문에 키는 들어 있지 않다.
                print(f"  ! 본문: {r.text[:500]}")
                for line in _hint(r.status_code, r.text):
                    print(f"  ! {line}")
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
