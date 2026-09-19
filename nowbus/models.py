"""코어 데이터 모델. 계획서 §3 그대로."""

from dataclasses import dataclass
from enum import StrEnum


class Catch(StrEnum):
    SAFE = "SAFE"  # 여유 있음
    TIGHT = "TIGHT"  # 걸어서 간신히. 여유 0~M_safe 분
    RUN = "RUN"  # 걸어선 못 잡지만 뛰면 잡힘 (여유 < 0, 뛰는 시간 기준으론 >= 0)
    MISS = "MISS"  # 못 잡음


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
    eta_min: float  # 도착까지 남은 분
    order: int  # 1차/2차 차량
    congestion: int | None = None
    is_last: bool = False
    is_estimated: bool = False  # headway로 추정한 값인지
    headway_min: float | None = None  # 같은 응답의 term. 3차 추정에 쓴다


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
    is_estimated: bool = False
    # 뛰어서 승차 정류장까지 가는 데 걸리는 시간. catch=RUN 일 때만 채운다.
    # 이때 margin_min 은 walk 가 아니라 이 값을 기준으로 잰 여유다.
    run_to_board_min: float | None = None


@dataclass(frozen=True, slots=True)
class RouteStopRow:
    """노선 경유 정류장 1행. 수집 배치가 route_stop 테이블에 그대로 넣는다."""

    route_id: str
    route_name: str
    stop_id: str
    ars_id: str | None
    stop_name: str
    lat: float
    lon: float
    seq: int
    direction: str | None


@dataclass(frozen=True, slots=True)
class PlanSet:
    """plan_now() 의 결과.

    두 목록을 **분리해서** 돌려주는 이유: 점수 하나로 섞으면 둘 중 하나가 반드시
    사라진다. 뛰어야 하는 후보는 총 소요시간이 압도적으로 짧아서(대기 0분) 벌점을
    얹지 않으면 화면을 독점하고, 벌점을 얹으면 상위 5개에서 밀려나 아예 안 보인다.
    실사용에서 후자였다 - '1~2분 뒤 도착하는 버스가 목록에 없어서 결국 다른 앱을
    다시 봤다'. 그래서 랭킹을 건드리지 않고 칸을 따로 준다.
    """

    best: list[Plan]
    sprint: list[Plan]

    def __bool__(self) -> bool:
        return bool(self.best or self.sprint)


@dataclass(frozen=True, slots=True)
class PlaceHit:
    """장소 검색 결과 1건. 즐겨찾기로 저장할 좌표의 출처다."""

    name: str
    lat: float
    lon: float
    address: str | None = None
    source: str = ""  # "stop" | "kakao-keyword" | "kakao-address"
    category: str | None = None
    distance_m: float | None = None
