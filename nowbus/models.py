"""코어 데이터 모델. 계획서 §3 그대로."""

from dataclasses import dataclass
from enum import StrEnum


class Catch(StrEnum):
    SAFE = "SAFE"    # 여유 있음
    TIGHT = "TIGHT"  # 뛰어야 함
    MISS = "MISS"    # 못 잡음


@dataclass(frozen=True, slots=True)
class Stop:
    stop_id: str
    ars_id: str | None
    name: str
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class Route:
    route_id: str
    route_name: str
    route_type: str | None = None
    headway_min: float | None = None


@dataclass(frozen=True, slots=True)
class Arrival:
    """실시간 도착 1건."""

    route_id: str
    stop_id: str
    eta_min: float           # 도착까지 남은 분
    order: int               # 1차/2차 차량
    congestion: int | None = None
    is_last: bool = False
    is_estimated: bool = False  # headway로 추정한 값인지


@dataclass(frozen=True, slots=True)
class Combo:
    """직통 조합 (정적)."""

    route_id: str
    board_stop_id: str
    alight_stop_id: str
    n_stops: int


@dataclass(frozen=True, slots=True)
class Plan:
    """최종 후보 1건."""

    board: Stop
    alight: Stop
    route: Route
    walk_to_board_min: float
    walk_from_alight_min: float
    eta_min: float
    ride_min: float
    total_min: float
    margin_min: float
    catch: Catch
    score: float
    congestion: int | None = None
    is_last: bool = False
