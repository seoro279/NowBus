import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fx():
    """실제 API 응답 원본. 스펙이 바뀌면 여기 물린 테스트가 먼저 깨진다."""

    def _read(name: str) -> str:
        return (FIXTURES / f"{name}.xml").read_text(encoding="utf-8")

    return _read
