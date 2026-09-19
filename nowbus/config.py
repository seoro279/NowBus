"""설정값. 계획서 §9의 초기 설정값을 그대로 반영한다."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NOWBUS_", extra="ignore")

    # --- 인증 ---
    seoul_api_key: str = ""
    api_token: str = "change-me"
    # 카카오 로컬(장소 검색) REST 키. 비어 있으면 정류장 이름 검색만 쓴다.
    kakao_rest_key: str = ""

    # --- 저장소 ---
    db_path: str = "data/nowbus.db"

    # --- 탐색 (설계서 D-6) ---
    radius_origin_m: int = 600
    radius_dest_m: int = 700
    # 실시간 API 호출 예산. 직통이 있는 승차 정류장 중 가까운 순으로 이만큼만
    # 조회한다. 정류장 후보 자체를 줄이는 값이 아니다 (D-10, Phase 7에서 재검토).
    max_origin_stops: int = 8
    # 하차 정류장에는 상한이 없다. 외부 호출이 없어서 줄일 이유가 없고,
    # 실측에서 12곳으로 제한했더니 강남역->시청 노선 9개 중 7개를 잃었다.

    # --- 판정 (설계서 D-7, §6.5) ---
    m_safe_min: float = 2.0
    walk_speed_m_per_min: float = 67.0
    walk_detour: float = 1.30
    walk_calib: float = 1.0  # 개인 보정계수. F-11 피드백으로 갱신
    ride_min_per_stop: float = 1.8

    # --- 뛰어서 잡기 (RUN 등급) ---
    # 걷는 속도로는 놓치지만 뛰면 잡히는 버스를 따로 보여준다.
    # 140 m/min = 8.4 km/h. 가방 메고 신호등 없는 구간을 달리는 속도로 잡았다.
    # 신호등 대기는 모델에 없다 - 그래서 RUN 은 본 목록에 섞지 않고 따로 낸다.
    run_speed_m_per_min: float = 140.0
    # 이 시간을 넘게 뛰어야 하면 후보에서 뺀다. 3분 이상 전력질주는 현실이 아니다.
    # 140 m/min 기준 약 420m, 도보로는 6분 거리까지가 상한이 된다.
    max_run_min: float = 3.0
    sprint_top_n: int = 3

    # --- 출력 (설계서 D-8, §6.4.1) ---
    top_n: int = 5
    max_per_stop: int = 2
    min_distinct_stops: int = 2

    # --- 점수 가중치 (설계서 §6.4) ---
    w_tight: float = 3.0
    w_walk: float = 0.2
    w_run: float = 8.0  # RUN 벌점. sprint 목록 내부 정렬에만 쓴다

    # --- 장소 검색 ---
    geocode_limit: int = 8

    # --- 인프라 ---
    cache_ttl_sec: int = 30
    http_timeout_sec: float = 3.0


settings = Settings()
