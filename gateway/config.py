"""All runtime config loaded from environment variables. Single source of truth for tunables."""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Postgres
    database_url: str = "postgresql+asyncpg://gateway:gateway@localhost:5432/gateway"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Rate limiting
    provider_rate_limit: int = 300       # calls per minute
    rate_limit_window_seconds: int = 60

    # Queue
    max_queue_depth: int = 10_000
    visibility_timeout_seconds: int = 30
    max_retries: int = 3
    starvation_max_wait_seconds: int = 60

    # Semantic cache
    similarity_threshold: float = 0.92
    embedding_model: str = "all-MiniLM-L6-v2"
    cache_ttl_seconds: int = 86_400      # 24h default

    # Classifier
    ollama_url: str = "http://localhost:11434"
    classifier_model: str = "qwen2.5:0.5b"

    # Worker pool
    worker_count: int = 4

    # WAL
    wal_path: str = "/tmp/gateway-wal"

    class Config:
        env_file = ".env"


settings = Settings()
