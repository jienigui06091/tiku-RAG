import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import Base, engine, get_db
from app.models import ChatMessage, ChatSession, Document, DocumentChunk, Library, Question
from app.schemas import (
    ChatExchangeOut,
    ChatMessageCreate,
    ChatMessageOut,
    ChatRequest,
    ChatResponse,
    ChatSessionCreate,
    ChatSessionOut,
    ChatSessionUpdate,
    Citation,
    ChunkingConfig,
    DocumentChunkOut,
    DocumentChunkPage,
    DocumentOut,
    LibraryCreate,
    LibraryOut,
    QuestionOut,
    QuestionPage,
    ReindexRequest,
    ReindexResult,
)
from app.services.documents import UnsupportedDocumentError, chunk_document, extract_text_from_bytes, parse_questions
from app.services.retrieval import delete_chunks, generate_answer, index_chunks, retrieve_chunks
from app.services.storage import ObjectStorageError, delete_upload, store_upload

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/api/health")
def health() -> dict[str, str | int]:
    return {
        "status": "ok",
        "vector_provider": settings.vector_provider,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
    }


@app.get("/api/libraries", response_model=list[LibraryOut])
def list_libraries(db: Session = Depends(get_db)):
    libraries = db.scalars(select(Library).order_by(Library.created_at.desc())).all()
    result: list[LibraryOut] = []
    for library in libraries:
        document_count = db.scalar(select(func.count(Document.id)).where(Document.library_id == library.id)) or 0
        question_count = db.scalar(select(func.count(Question.id)).where(Question.library_id == library.id)) or 0
        chunk_count = db.scalar(select(func.count(DocumentChunk.id)).where(DocumentChunk.library_id == library.id)) or 0
        result.append(_library_out(library, document_count, question_count, chunk_count))
    return result


@app.post("/api/libraries", response_model=LibraryOut, status_code=201)
def create_library(payload: LibraryCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(Library).where(Library.name == payload.name))
    if existing:
        raise HTTPException(status_code=409, detail="Library name already exists")
    library = Library(**payload.model_dump())
    db.add(library)
    db.commit()
    db.refresh(library)
    return _library_out(library, 0, 0, 0)


@app.delete("/api/libraries/{library_id}", status_code=204, response_class=Response)
async def delete_library(library_id: str, db: Session = Depends(get_db)):
    library = _get_library(db, library_id)
    documents = db.scalars(select(Document).where(Document.library_id == library_id)).all()
    document_ids = [document.id for document in documents]
    storage_paths = [document.storage_path for document in documents]

    await delete_chunks(document_ids)
    db.execute(
        update(ChatSession)
        .where(ChatSession.library_id == library_id)
        .values(library_id=None, updated_at=datetime.now(timezone.utc))
    )
    db.delete(library)
    db.commit()

    for storage_path in storage_paths:
        try:
            delete_upload(storage_path)
        except (ObjectStorageError, OSError):
            # Database records are already gone; a failed object cleanup should not restore the library.
            pass


@app.get("/api/libraries/{library_id}/documents", response_model=list[DocumentOut])
def list_documents(library_id: str, db: Session = Depends(get_db)):
    _get_library(db, library_id)
    documents = db.scalars(
        select(Document).where(Document.library_id == library_id).order_by(Document.created_at.desc())
    ).all()
    return [_document_out(db, document) for document in documents]


@app.post("/api/libraries/{library_id}/documents", response_model=DocumentOut, status_code=201)
async def upload_document(
    library_id: str,
    file: UploadFile = File(...),
    chunk_model: str = Form(default="interface"),
    chunk_size: int = Form(default=settings.chunk_size),
    chunk_overlap: int = Form(default=settings.chunk_overlap),
    retain_context: bool = Form(default=True),
    split_by_page: bool = Form(default=True),
    custom_delimiter: str = Form(default=""),
    db: Session = Depends(get_db),
):
    _get_library(db, library_id)
    original_name = Path(file.filename or "upload").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise HTTPException(status_code=400, detail="Only PDF, DOCX, TXT, and MD files are supported")

    payload = await file.read()
    if len(payload) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File size cannot exceed {settings.max_upload_mb} MB")

    try:
        chunking = ChunkingConfig(
            chunk_model=chunk_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            retain_context=retain_context,
            split_by_page=split_by_page,
            custom_delimiter=custom_delimiter,
        )
        chunking.validate()
        storage_key = f"libraries/{library_id}/documents/{uuid.uuid4()}{suffix}"
        storage_path = store_upload(storage_key, payload, file.content_type)
        document = Document(
            library_id=library_id,
            filename=original_name,
            storage_path=storage_path,
            mime_type=file.content_type,
        )
        db.add(document)
        db.flush()

        raw_text, page_count = extract_text_from_bytes(payload, suffix)
        document.raw_text = raw_text
        document.page_count = page_count

        parsed_questions = parse_questions(raw_text)
        for parsed in parsed_questions:
            db.add(
                Question(
                    library_id=library_id,
                    document_id=document.id,
                    sequence=parsed.sequence,
                    stem=parsed.stem,
                    options=parsed.options,
                    answer=parsed.answer,
                    analysis=parsed.analysis,
                    source_page=parsed.source_page,
                )
            )

        chunks = _create_chunks(document, raw_text, chunking)
        db.add_all(chunks)
        db.flush()
        await index_chunks(chunks)
        document.status = "ready"
        db.commit()
    except (ObjectStorageError, UnsupportedDocumentError, ValueError, OSError, httpx.HTTPError, ImportError) as error:
        if "document" in locals():
            document.status = "failed"
            document.error_message = f"{type(error).__name__}: {error}"
            db.commit()
        raise HTTPException(
            status_code=422,
            detail=f"Document ingestion failed: {type(error).__name__}: {error}",
        ) from error

    db.refresh(document)
    return _document_out(db, document)


@app.post("/api/libraries/{library_id}/reindex", response_model=ReindexResult)
async def reindex_library(
    library_id: str,
    payload: ReindexRequest | None = None,
    db: Session = Depends(get_db),
):
    _get_library(db, library_id)
    payload = payload or ReindexRequest(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    try:
        payload.validate()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    documents = db.scalars(
        select(Document).where(Document.library_id == library_id, Document.raw_text.is_not(None))
    ).all()
    document_ids = [document.id for document in documents]
    await delete_chunks(document_ids)
    db.execute(delete(DocumentChunk).where(Document.document_id.in_(document_ids)))

    chunks: list[DocumentChunk] = []
    for document in documents:
        chunks.extend(_create_chunks(document, document.raw_text or "", payload))
    db.add_all(chunks)
    db.flush()
    await index_chunks(chunks)
    for document in documents:
        document.status = "ready"
        document.error_message = None
    db.commit()
    return ReindexResult(documents=len(documents), chunks=len(chunks))


@app.get("/api/libraries/{library_id}/questions", response_model=QuestionPage)
def list_questions(
    library_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = None,
    db: Session = Depends(get_db),
):
    _get_library(db, library_id)
    statement = select(Question).options(joinedload(Question.document)).where(Question.library_id == library_id)
    if keyword:
        statement = statement.where(Question.stem.contains(keyword))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    questions = db.scalars(
        statement.order_by(Question.sequence.asc(), Question.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique().all()
    return QuestionPage(
        items=[_question_out(question) for question in questions],
        total=total,
        page=page,
        page_size=page_size,
    )


@app.get("/api/libraries/{library_id}/chunks", response_model=DocumentChunkPage)
def list_document_chunks(
    library_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = None,
    db: Session = Depends(get_db),
):
    _get_library(db, library_id)
    statement = (
        select(DocumentChunk)
        .join(DocumentChunk.document)
        .options(joinedload(DocumentChunk.document))
        .where(DocumentChunk.library_id == library_id)
    )
    if keyword:
        statement = statement.where(
            or_(
                DocumentChunk.content.contains(keyword),
                DocumentChunk.chapter.contains(keyword),
                Document.filename.contains(keyword),
            )
        )
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    chunks = db.scalars(
        statement.order_by(Document.created_at.desc(), DocumentChunk.sequence.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique().all()
    return DocumentChunkPage(
        items=[_document_chunk_out(chunk) for chunk in chunks],
        total=total,
        page=page,
        page_size=page_size,
    )


@app.get("/api/chats", response_model=list[ChatSessionOut])
def list_chat_sessions(db: Session = Depends(get_db)):
    chats = db.scalars(
        select(ChatSession)
        .options(joinedload(ChatSession.library))
        .order_by(ChatSession.updated_at.desc(), ChatSession.created_at.desc())
    ).unique().all()
    return [_chat_session_out(chat) for chat in chats]


@app.post("/api/chats", response_model=ChatSessionOut, status_code=201)
def create_chat_session(payload: ChatSessionCreate, db: Session = Depends(get_db)):
    if payload.library_id:
        _get_library(db, payload.library_id)
    chat = ChatSession(**payload.model_dump())
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return _chat_session_out(chat)


@app.patch("/api/chats/{chat_id}", response_model=ChatSessionOut)
def update_chat_session(
    chat_id: str,
    payload: ChatSessionUpdate,
    db: Session = Depends(get_db),
):
    chat = _get_chat_session(db, chat_id)
    if payload.title is not None:
        chat.title = payload.title
    if "library_id" in payload.model_fields_set:
        if payload.library_id:
            _get_library(db, payload.library_id)
        chat.library_id = payload.library_id
    chat.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(chat)
    return _chat_session_out(chat)


@app.delete("/api/chats/{chat_id}", status_code=204, response_class=Response)
def delete_chat_session(chat_id: str, db: Session = Depends(get_db)):
    db.delete(_get_chat_session(db, chat_id))
    db.commit()


@app.get("/api/chats/{chat_id}/messages", response_model=list[ChatMessageOut])
def list_chat_messages(chat_id: str, db: Session = Depends(get_db)):
    _get_chat_session(db, chat_id)
    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.chat_session_id == chat_id)
        .order_by(ChatMessage.created_at.asc())
    ).all()
    return [_chat_message_out(message) for message in messages]


@app.post("/api/chats/{chat_id}/messages", response_model=ChatExchangeOut)
async def send_chat_message(
    chat_id: str,
    payload: ChatMessageCreate,
    db: Session = Depends(get_db),
):
    chat = _get_chat_session(db, chat_id)
    if not chat.library_id:
        raise HTTPException(status_code=422, detail="Attach a knowledge base before sending a message")

    matches = await retrieve_chunks(db, payload.query, chat.library_id, None, payload.top_k)
    answer, mode = await generate_answer(payload.query, matches)
    citations = [
        Citation(
            id=chunk.id,
            document_name=chunk.document.filename if chunk.document else None,
            chunk_sequence=chunk.sequence,
            source_page_start=chunk.source_page_start,
            source_page_end=chunk.source_page_end,
            chapter=chunk.chapter,
            score=round(score, 3),
        )
        for chunk, score in matches
    ]
    user_message = ChatMessage(
        chat_session_id=chat.id,
        role="user",
        content=payload.query,
    )
    assistant_message = ChatMessage(
        chat_session_id=chat.id,
        role="assistant",
        content=answer,
        citations=[citation.model_dump() for citation in citations],
    )
    chat.updated_at = datetime.now(timezone.utc)
    db.add_all([user_message, assistant_message])
    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)
    return ChatExchangeOut(
        user_message=_chat_message_out(user_message),
        assistant_message=_chat_message_out(assistant_message),
        retrieval_mode=mode,
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    matches = await retrieve_chunks(db, payload.query, payload.library_id, payload.chapter, payload.top_k)
    answer, mode = await generate_answer(payload.query, matches)
    citations = [
        Citation(
            id=chunk.id,
            document_name=chunk.document.filename if chunk.document else None,
            chunk_sequence=chunk.sequence,
            source_page_start=chunk.source_page_start,
            source_page_end=chunk.source_page_end,
            chapter=chunk.chapter,
            score=round(score, 3),
        )
        for chunk, score in matches
    ]
    return ChatResponse(answer=answer, citations=citations, retrieval_mode=mode)


def _create_chunks(
    document: Document,
    raw_text: str,
    chunking: ChunkingConfig | None = None,
) -> list[DocumentChunk]:
    config = chunking or ChunkingConfig(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    config.validate()
    return [
        DocumentChunk(
            library_id=document.library_id,
            document_id=document.id,
            sequence=draft.sequence,
            content=draft.content,
            chapter=draft.chapter,
            source_page_start=draft.source_page_start,
            source_page_end=draft.source_page_end,
            char_start=draft.char_start,
            char_end=draft.char_end,
        )
        for draft in chunk_document(
            raw_text,
            config.chunk_size,
            config.chunk_overlap,
            config.chunk_model,
            config.retain_context,
            config.split_by_page,
            config.custom_delimiter,
        )
    ]


def _get_library(db: Session, library_id: str) -> Library:
    library = db.get(Library, library_id)
    if not library:
        raise HTTPException(status_code=404, detail="Library not found")
    return library


def _get_chat_session(db: Session, chat_id: str) -> ChatSession:
    chat = db.scalar(
        select(ChatSession)
        .options(joinedload(ChatSession.library))
        .where(ChatSession.id == chat_id)
    )
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


def _library_out(library: Library, document_count: int, question_count: int, chunk_count: int) -> LibraryOut:
    return LibraryOut(
        id=library.id,
        name=library.name,
        subject=library.subject,
        description=library.description,
        created_at=library.created_at,
        document_count=document_count,
        question_count=question_count,
        chunk_count=chunk_count,
    )


def _document_out(db: Session, document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        filename=document.filename,
        mime_type=document.mime_type,
        page_count=document.page_count,
        status=document.status,
        error_message=document.error_message,
        created_at=document.created_at,
        question_count=db.scalar(select(func.count(Question.id)).where(Question.document_id == document.id)) or 0,
        chunk_count=db.scalar(select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document.id)) or 0,
    )


def _question_out(question: Question) -> QuestionOut:
    return QuestionOut(
        id=question.id,
        sequence=question.sequence,
        question_type=question.question_type,
        chapter=question.chapter,
        stem=question.stem,
        options=question.options,
        answer=question.answer,
        analysis=question.analysis,
        source_page=question.source_page,
        document_name=question.document.filename if question.document else None,
    )


def _document_chunk_out(chunk: DocumentChunk) -> DocumentChunkOut:
    return DocumentChunkOut(
        id=chunk.id,
        sequence=chunk.sequence,
        content=chunk.content,
        chapter=chunk.chapter,
        source_page_start=chunk.source_page_start,
        source_page_end=chunk.source_page_end,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        document_name=chunk.document.filename if chunk.document else None,
    )


def _chat_session_out(chat: ChatSession) -> ChatSessionOut:
    return ChatSessionOut(
        id=chat.id,
        title=chat.title,
        library_id=chat.library_id,
        library_name=chat.library.name if chat.library else None,
        created_at=chat.created_at,
        updated_at=chat.updated_at,
    )


def _chat_message_out(message: ChatMessage) -> ChatMessageOut:
    return ChatMessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        citations=[Citation.model_validate(citation) for citation in (message.citations or [])],
        created_at=message.created_at,
    )
