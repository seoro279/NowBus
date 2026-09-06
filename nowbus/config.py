"""설정값. 계획서 §9의 초기 설정값을 그대로 반영한다."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="NOWBUS_", extra="ignore"
    )

    # --- 인증 ---
    seoul_api_key: str = ""
    api_token: str = "change-me"

    # --- 저장소 ---
    db_path: str = "data/nowbus.db"

    # --- 탐색 (설계서 D-6) ---
    radius_origin_m: int = 600
    radius_dest_m: int = 700
    max_origin_stops: int = 8   # API 호출 예산. D-10, Phase 7에서 재검토
    max_dest_stops: int = 12

    # --- 판정 (설계서 D-7, §6.5) ---
    m_safe_min: float = 2.0
    walk_speed_m_per_min: float = 67.0
    walk_detour: float = 1.30
    walk_calib: float = 1.0      # 개인 보정계수. F-11 피드백으로 갱신
    ride_min_per_stop: float = 1.8

    # --- 출력 (설계서 D-8, §6.4.1) ---
    top_n: int = 5
    max_per_stop: int = 2
    min_distinct_stops: int = 2

    # --- 점수 가중치 (설계서 §6.4) ---
    w_tight: float = 3.0
    w_walk: float = 0.2

    # --- 인프라 ---
    cache_ttl_sec: int = 30
    http_timeout_sec: float = 3.0


settings = Settings()
