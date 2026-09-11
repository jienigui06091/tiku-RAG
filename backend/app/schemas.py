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


class DocumentChunkPage(BaseModel):
    items: list[DocumentChunkOut]
    total: int
    page: int
    page_size: int


class ChatSessionCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=120)
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
