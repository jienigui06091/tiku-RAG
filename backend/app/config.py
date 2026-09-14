from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Tiku RAG"
    app_env: str = "development"
    cors_origins: str = "http://localhost:5173"
    data_dir: Path = Path("./data")
    database_url: str = "sqlite:///./data/tiku-rag.db"
    session_cookie_name: str = "tiku_rag_session"
    session_ttl_hours: int = 72
    session_cookie_secure: bool = False
    settings_encryption_key: str | None = None
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = None
    storage_provider: str = "local"
    minio_endpoint: str | None = None
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    minio_bucket: str | None = None
    minio_secure: bool = False
    vector_provider: str = "local"
    milvus_uri: str | None = None
    milvus_token: str | None = None
    milvus_collection: str = "tiku_document_chunks"
    chunk_size: int = 800
    chunk_overlap: int = 120
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str = "Qwen3-Embedding-0.6B"
    embedding_batch_size: int = 8
    embedding_timeout_seconds: float = 120
    rerank_base_url: str | None = None
    rerank_api_key: str | None = None
    rerank_model: str = "Qwen3-Reranker-0.6B"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_temperature: float = 0.1
    ocr_provider: str = "none"
    ocr_base_url: str | None = None
    ocr_api_key: str | None = None
    max_upload_mb: int = 50

    model_config = SettingsConfigDict(
        env_file="../.env",
        extra="ignore",
        protected_namespaces=("model_",),
    )

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.storage_provider.lower() == "local":
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings
