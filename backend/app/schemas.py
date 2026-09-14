from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LibraryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    subject: str | None = Field(default=None, max_length=120)
    description: str | None = None


class LibraryOut(BaseModel):
    id: str
    name: str
    subject: str | None
    description: str | None
    created_at: datetime | None
    document_count: int = 0
    question_count: int = 0
    chunk_count: int = 0


class DocumentOut(BaseModel):
    id: str
    filename: str
    mime_type: str | None
    page_count: int | None
    status: str
    processing_stage: str
    progress: int
    error_message: str | None
    created_at: datetime | None
    question_count: int = 0
    chunk_count: int = 0


class ChunkingConfig(BaseModel):
    chunk_model: Literal["interface", "structured", "fixed", "delimiter"] = "interface"
    chunk_size: int = Field(default=800, ge=100, le=5000)
    chunk_overlap: int = Field(default=120, ge=0, le=2000)
    retain_context: bool = True
    split_by_page: bool = True
    custom_delimiter: str = Field(default="", max_length=200)

    def validate_overlap(self) -> None:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")

    def validate(self) -> None:
        self.validate_overlap()
        if self.chunk_model == "delimiter" and not self.custom_delimiter:
            raise ValueError("custom_delimiter is required when chunk_model is delimiter")


class QuestionOut(BaseModel):
    id: str
    sequence: int | None
    question_type: str | None
    chapter: str | None
    stem: str
    options: list[str] | None
    answer: str | None
    analysis: str | None
    source_page: int | None
    document_name: str | None = None


class QuestionPage(BaseModel):
    items: list[QuestionOut]
    total: int
    page: int
    page_size: int


class DocumentChunkOut(BaseModel):
    id: str
    sequence: int
    content: str
    chapter: str | None
    source_page_start: int | None
    source_page_end: int | None
    char_start: int
    char_end: int
    document_name: str | None = None


class DocumentChunkSummaryOut(BaseModel):
    id: str
    sequence: int
    content_preview: str
    chapter: str | None
    source_page_start: int | None
    source_page_end: int | None
    document_name: str | None = None


class DocumentChunkPage(BaseModel):
    items: list[DocumentChunkSummaryOut]
    total: int
    page: int
    page_size: int


class ChatSessionCreate(BaseModel):
    library_id: str | None = None


class ChatSessionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    library_id: str | None = None


class ChatSessionOut(BaseModel):
    id: str
    title: str
    library_id: str | None
    library_name: str | None
    created_at: datetime | None
    updated_at: datetime | None


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    library_id: str | None = None
    chapter: str | None = None
    top_k: int = Field(default=5, ge=1, le=10)


class Citation(BaseModel):
    id: str
    document_name: str | None
    chunk_sequence: int
    source_page_start: int | None
    source_page_end: int | None
    chapter: str | None
    score: float


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    citations: list[Citation] = []
    created_at: datetime | None


class ChatMessageCreate(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)


class ChatExchangeOut(BaseModel):
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut
    retrieval_mode: str


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    retrieval_mode: str


class ReindexRequest(ChunkingConfig):
    pass


class ReindexResult(BaseModel):
    documents: int
    chunks: int


class BootstrapStatusOut(BaseModel):
    ready: bool


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    role: Literal["super_admin", "member"]
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime | None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=256)
    role: Literal["super_admin", "member"] = "member"


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    password: str | None = Field(default=None, min_length=8, max_length=256)
    role: Literal["super_admin", "member"] | None = None
    is_active: bool | None = None


class SystemSettingsOut(BaseModel):
    storage_provider: Literal["local", "minio"]
    minio_endpoint: str | None
    minio_bucket: str | None
    minio_secure: bool
    vector_provider: Literal["local", "milvus"]
    milvus_uri: str | None
    milvus_collection: str
    chunk_size: int = Field(ge=100, le=5000)
    chunk_overlap: int = Field(ge=0, le=2000)
    embedding_base_url: str | None
    embedding_model: str
    embedding_batch_size: int = Field(ge=1, le=256)
    embedding_timeout_seconds: float = Field(ge=1, le=600)
    rerank_base_url: str | None
    rerank_model: str
    llm_base_url: str | None
    llm_model: str | None
    llm_temperature: float = Field(ge=0, le=2)
    ocr_provider: str
    ocr_base_url: str | None
    max_upload_mb: int = Field(ge=1, le=2048)
    minio_access_key_configured: bool
    minio_secret_key_configured: bool
    milvus_token_configured: bool
    embedding_api_key_configured: bool
    rerank_api_key_configured: bool
    llm_api_key_configured: bool
    ocr_api_key_configured: bool


class SystemSettingsUpdate(BaseModel):
    storage_provider: Literal["local", "minio"] | None = None
    minio_endpoint: str | None = None
    minio_bucket: str | None = None
    minio_secure: bool | None = None
    minio_access_key: str | None = Field(default=None, max_length=512)
    minio_secret_key: str | None = Field(default=None, max_length=512)
    vector_provider: Literal["local", "milvus"] | None = None
    milvus_uri: str | None = None
    milvus_collection: str | None = Field(default=None, min_length=1, max_length=200)
    milvus_token: str | None = Field(default=None, max_length=1024)
    chunk_size: int | None = Field(default=None, ge=100, le=5000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=2000)
    embedding_base_url: str | None = None
    embedding_api_key: str | None = Field(default=None, max_length=1024)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_batch_size: int | None = Field(default=None, ge=1, le=256)
    embedding_timeout_seconds: float | None = Field(default=None, ge=1, le=600)
    rerank_base_url: str | None = None
    rerank_api_key: str | None = Field(default=None, max_length=1024)
    rerank_model: str | None = Field(default=None, min_length=1, max_length=200)
    llm_base_url: str | None = None
    llm_api_key: str | None = Field(default=None, max_length=1024)
    llm_model: str | None = Field(default=None, max_length=200)
    llm_temperature: float | None = Field(default=None, ge=0, le=2)
    ocr_provider: str | None = Field(default=None, max_length=80)
    ocr_base_url: str | None = None
    ocr_api_key: str | None = Field(default=None, max_length=1024)
    max_upload_mb: int | None = Field(default=None, ge=1, le=2048)
