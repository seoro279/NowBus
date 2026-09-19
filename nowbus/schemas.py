"""API 계약. 프론트와 서버가 공유하는 유일한 약속.

모든 시간 필드는 **정수 분**이다. 프론트에서 반올림 로직을 갖지 않게 한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlanItem(BaseModel):
    rank: int
    board_stop_name: str
    board_stop_ars: str | None
    board_lat: float  # 지도앱 딥링크용 [F-18]
    board_lon: float
    walk_to_board_min: int
    route_name: str
    eta_min: int
    margin_min: int  # catch=RUN 이면 '뛰었을 때'의 여유다
    catch: Literal["SAFE", "TIGHT", "RUN", "MISS"]
    run_to_board_min: int | None = None  # RUN 후보에만 채워진다
    alight_stop_name: str
    walk_from_alight_min: int
    ride_min: int
    total_min: int
    arrive_at: str  # "09:14"
    congestion: int | None = None
    is_last: bool = False
    is_estimated: bool = False


class PlanResponse(BaseModel):
    origin_label: str  # "현재 위치" | "집"
    dest_label: str
    departed_at: str  # "08:41"
    generated_at: str  # ISO8601. 클라이언트가 '몇 초 전'을 표시한다
    items: list[PlanItem]
    # 걸어선 놓치지만 뛰면 잡히는 버스. items 와 **겹칠 수 있다** - 같은 정류장의
    # 같은 노선이 '뛰면 1분 뒤 차 / 걸으면 9분 뒤 차'로 양쪽에 나오는 게 정상이다.
    sprint: list[PlanItem] = []
    warning: str | None = None


class Place(BaseModel):
    name: str
    lat: float
    lon: float


class PlaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PlaceHitOut(BaseModel):
    """장소 검색 결과 1건. 프론트는 이걸 그대로 /api/places 로 넘긴다."""

    name: str
    lat: float
    lon: float
    address: str | None = None
    source: str = ""
    category: str | None = None
    distance_m: int | None = None


class Feedback(BaseModel):
    """실측 도보시간 기록 [F-11]. 도보 보정계수 학습에 쓴다."""

    stop_id: str
    predicted_walk_min: float
    actual_walk_min: float
