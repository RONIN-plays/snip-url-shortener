from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (12-factor style),
    so the same image runs locally, in tests and in production."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- infrastructure ---
    database_url: str = "postgresql://shortener:change-me@localhost:5432/shortener"
    redis_url: str = "redis://localhost:6379/0"
    base_url: str = "http://localhost:8000"

    # --- secrets ---
    id_secret: str = "dev-only-secret"          # keys the short-code scrambler + IP hashing
    jwt_secret: str = "dev-only-jwt-secret"
    jwt_minutes: int = 60

    # --- Postgres pool (per API process; workers x max must stay < max_connections) ---
    db_pool_min: int = 2
    db_pool_max: int = 10
    db_timeout: float = 3.0          # max seconds for one query
    db_acquire_timeout: float = 1.0  # max seconds to wait for a pooled connection

    # --- circuit breakers (Redis and Postgres) ---
    breaker_threshold: int = 3       # consecutive failures before the breaker opens
    breaker_reset: float = 2.0       # seconds to stay open before probing again

    # --- Redis ---
    redis_timeout: float = 0.25      # short on purpose: a slow Redis must not slow redirects

    # --- cache (M2) ---
    cache_enabled: bool = True       # switch off to benchmark the "before" case
    cache_ttl: int = 3600            # seconds
    negative_ttl: int = 30           # how long "this code doesn't exist" is remembered

    # --- rate limiting (M3): token bucket = burst size + refill rate per second ---
    rate_limit_enabled: bool = True
    rl_redirect_burst: float = 100
    rl_redirect_rps: float = 50
    rl_create_user_burst: float = 30
    rl_create_user_rps: float = 1
    rl_create_anon_burst: float = 5
    rl_create_anon_rps: float = 0.1
    rl_auth_burst: float = 10        # register/login: slows password guessing
    rl_auth_rps: float = 0.2

    # --- analytics (M4) ---
    click_stream: str = "clicks"
    click_stream_maxlen: int = 100_000   # cap so a dead worker can't fill Redis memory


settings = Settings()
