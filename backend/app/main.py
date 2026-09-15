import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, inspect, or_, select, text, update
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import Base, SessionLocal, engine, get_db
from app.models import AuditLog, ChatMessage, ChatSession, Document, DocumentChunk, Library, Question, User
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
    BootstrapStatusOut,
    DocumentChunkOut,
    DocumentChunkPage,
    DocumentChunkSummaryOut,
    DocumentOut,
    LibraryCreate,
    LibraryOut,
    QuestionOut,
    QuestionPage,
    ReindexRequest,
    ReindexResult,
    LoginRequest,
    LlmSettingsOut,
    LlmSettingsUpdate,
    SystemSettingsOut,
    SystemSettingsUpdate,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services.auth import create_session, get_current_user, hash_password, require_super_admin, revoke_session, verify_password
from app.services.documents import chunk_document, extract_text_from_bytes, parse_questions
from app.services.retrieval import delete_chunks, generate_answer, index_chunks, retrieve_chunks, stream_answer
from app.services.storage import ObjectStorageError, delete_upload, store_upload
from app.services.runtime_config import (
    RuntimeConfigError,
    get_llm_settings_out,
    get_runtime_settings,
    get_system_settings_out,
    save_llm_settings,
    save_system_settings,
)

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
    _ensure_document_progress_columns()
    _ensure_ownership_columns()
    _bootstrap_admin()


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, str | int]:
    runtime = get_runtime_settings(db)
    return {
        "status": "ok",
        "vector_provider": runtime.vector_provider,
        "chunk_size": runtime.chunk_size,
        "chunk_overlap": runtime.chunk_overlap,
    }


@app.get("/api/auth/bootstrap-status", response_model=BootstrapStatusOut)
def bootstrap_status(db: Session = Depends(get_db)):
    return BootstrapStatusOut(ready=bool(db.scalar(select(func.count(User.id)))))


@app.post("/api/auth/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username.strip()))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_session(db, user)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    _audit(db, user, "auth.login", "user", user.id)
    return _user_out(user)


@app.post("/api/auth/logout", status_code=204, response_class=Response)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    revoke_session(db, request.cookies.get(settings.session_cookie_name))
    response.delete_cookie(settings.session_cookie_name)


@app.get("/api/auth/me", response_model=UserOut)
def current_user(user: User = Depends(get_current_user)):
    return _user_out(user)


@app.get("/api/admin/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_super_admin)):
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return [_user_out(user) for user in users]


@app.post("/api/admin/users", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), actor: User = Depends(require_super_admin)):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail="Username already exists")
    user = User(
        username=payload.username,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    _audit(db, actor, "user.create", "user", user.id, {"username": user.username, "role": user.role})
    return _user_out(user)


@app.patch("/api/admin/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_super_admin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == actor.id and payload.is_active is False:
        raise HTTPException(status_code=422, detail="You cannot disable the current account")
    if user.role == "super_admin" and payload.role == "member" and _active_super_admin_count(db) <= 1:
        raise HTTPException(status_code=422, detail="At least one active super administrator is required")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "password":
            user.password_hash = hash_password(value)
        else:
            setattr(user, field, value)
    db.commit()
    db.refresh(user)
    _audit(db, actor, "user.update", "user", user.id, {"fields": sorted(payload.model_fields_set)})
    return _user_out(user)


@app.get("/api/admin/settings", response_model=SystemSettingsOut)
def get_system_settings(db: Session = Depends(get_db), _: User = Depends(require_super_admin)):
    try:
        return get_system_settings_out(db)
    except RuntimeConfigError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.put("/api/admin/settings", response_model=SystemSettingsOut)
def update_system_settings(
    payload: SystemSettingsUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_super_admin),
):
    try:
        result = save_system_settings(db, payload, actor)
    except (RuntimeConfigError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _audit(db, actor, "settings.update", "system_settings", None, {"fields": sorted(payload.model_fields_set)})
    return result


@app.get("/api/settings/llm", response_model=LlmSettingsOut)
def get_llm_settings(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    try:
        return get_llm_settings_out(db)
    except RuntimeConfigError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.put("/api/settings/llm", response_model=LlmSettingsOut)
def update_llm_settings(
    payload: LlmSettingsUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    try:
        result = save_llm_settings(db, payload, actor)
    except (RuntimeConfigError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _audit(db, actor, "settings.llm.update", "system_settings", None, {"fields": sorted(payload.model_fields_set)})
    return result


@app.get("/api/libraries", response_model=list[LibraryOut])
def list_libraries(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    libraries = db.scalars(_library_statement(user).order_by(Library.created_at.desc())).all()
    result: list[LibraryOut] = []
    for library in libraries:
        document_count = db.scalar(select(func.count(Document.id)).where(Document.library_id == library.id)) or 0
        question_count = db.scalar(select(func.count(Question.id)).where(Question.library_id == library.id)) or 0
        chunk_count = db.scalar(select(func.count(DocumentChunk.id)).where(DocumentChunk.library_id == library.id)) or 0
        result.append(_library_out(library, document_count, question_count, chunk_count))
    return result


@app.post("/api/libraries", response_model=LibraryOut, status_code=201)
def create_library(payload: LibraryCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    existing = db.scalar(select(Library).where(Library.name == payload.name, Library.owner_id == user.id))
    if existing:
        raise HTTPException(status_code=409, detail="Library name already exists")
    library = Library(**payload.model_dump(), owner_id=user.id)
    db.add(library)
    db.commit()
    db.refresh(library)
    return _library_out(library, 0, 0, 0)


@app.delete("/api/libraries/{library_id}", status_code=204, response_class=Response)
async def delete_library(library_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    library = _get_library(db, library_id, user)
    documents = db.scalars(select(Document).where(Document.library_id == library_id)).all()
    document_ids = [document.id for document in documents]
    storage_paths = [document.storage_path for document in documents]

    await delete_chunks(db, document_ids)
    db.execute(
        update(ChatSession)
        .where(ChatSession.library_id == library_id)
        .values(library_id=None, updated_at=datetime.now(timezone.utc))
    )
    db.delete(library)
    db.commit()

    for storage_path in storage_paths:
        try:
            delete_upload(db, storage_path)
        except (ObjectStorageError, OSError):
            # Database records are already gone; a failed object cleanup should not restore the library.
            pass


@app.get("/api/libraries/{library_id}/documents", response_model=list[DocumentOut])
def list_documents(library_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _get_library(db, library_id, user)
    documents = db.scalars(
        select(Document).where(Document.library_id == library_id).order_by(Document.created_at.desc())
    ).all()
    return [_document_out(db, document) for document in documents]


@app.post("/api/libraries/{library_id}/documents", response_model=DocumentOut, status_code=202)
async def upload_document(
    library_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    chunk_model: str = Form(default="interface"),
    chunk_size: int | None = Form(default=None),
    chunk_overlap: int | None = Form(default=None),
    retain_context: bool = Form(default=True),
    split_by_page: bool = Form(default=True),
    custom_delimiter: str = Form(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_library(db, library_id, user)
    runtime = get_runtime_settings(db)
    original_name = Path(file.filename or "upload").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt", ".md"}:
        raise HTTPException(status_code=400, detail="Only PDF, DOCX, TXT, and MD files are supported")

    payload = await file.read()
    if len(payload) > runtime.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File size cannot exceed {runtime.max_upload_mb} MB")

    try:
        chunking = ChunkingConfig(
            chunk_model=chunk_model,
            chunk_size=chunk_size or runtime.chunk_size,
            chunk_overlap=chunk_overlap if chunk_overlap is not None else runtime.chunk_overlap,
            retain_context=retain_context,
            split_by_page=split_by_page,
            custom_delimiter=custom_delimiter,
        )
        chunking.validate()
        storage_key = f"libraries/{library_id}/documents/{uuid.uuid4()}{suffix}"
        storage_path = store_upload(db, storage_key, payload, file.content_type)
        document = Document(
            library_id=library_id,
            filename=original_name,
            storage_path=storage_path,
            mime_type=file.content_type,
            status="processing",
            processing_stage="queued",
            progress=0,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
    except (ObjectStorageError, ValueError, OSError) as error:
        raise HTTPException(
            status_code=422,
            detail=f"Document ingestion failed: {type(error).__name__}: {error}",
        ) from error

    background_tasks.add_task(_ingest_document, document.id, payload, suffix, chunking)
    return _document_out(db, document)


@app.post("/api/libraries/{library_id}/reindex", response_model=ReindexResult)
async def reindex_library(
    library_id: str,
    payload: ReindexRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_library(db, library_id, user)
    runtime = get_runtime_settings(db)
    payload = payload or ReindexRequest(
        chunk_size=runtime.chunk_size,
        chunk_overlap=runtime.chunk_overlap,
    )
    try:
        payload.validate()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    documents = db.scalars(
        select(Document).where(Document.library_id == library_id, Document.raw_text.is_not(None))
    ).all()
    document_ids = [document.id for document in documents]
    await delete_chunks(db, document_ids)
    db.execute(delete(DocumentChunk).where(Document.document_id.in_(document_ids)))

    chunks: list[DocumentChunk] = []
    for document in documents:
        chunks.extend(_create_chunks(document, document.raw_text or "", payload))
    db.add_all(chunks)
    db.flush()
    await index_chunks(db, chunks)
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
    user: User = Depends(get_current_user),
):
    _get_library(db, library_id, user)
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
    user: User = Depends(get_current_user),
):
    _get_library(db, library_id, user)
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
        items=[_document_chunk_summary_out(chunk) for chunk in chunks],
        total=total,
        page=page,
        page_size=page_size,
    )


@app.get("/api/libraries/{library_id}/chunks/{chunk_id}", response_model=DocumentChunkOut)
def get_document_chunk(
    library_id: str,
    chunk_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _get_library(db, library_id, user)
    chunk = db.scalar(
        select(DocumentChunk)
        .options(joinedload(DocumentChunk.document))
        .where(
            DocumentChunk.id == chunk_id,
            DocumentChunk.library_id == library_id,
        )
    )
    if not chunk:
        raise HTTPException(status_code=404, detail="Document chunk not found")
    return _document_chunk_out(chunk)


@app.get("/api/chats", response_model=list[ChatSessionOut])
def list_chat_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    chats = db.scalars(
        select(ChatSession)
        .options(joinedload(ChatSession.library))
        .where(*_chat_owner_filters(user))
        .order_by(ChatSession.updated_at.desc(), ChatSession.created_at.desc())
    ).unique().all()
    return [_chat_session_out(chat) for chat in chats]


@app.post("/api/chats", response_model=ChatSessionOut, status_code=201)
def create_chat_session(
    payload: ChatSessionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.library_id:
        _get_library(db, payload.library_id, user)
    chat = ChatSession(title="新建聊天", **payload.model_dump())
    chat.user_id = user.id
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return _chat_session_out(chat)


@app.patch("/api/chats/{chat_id}", response_model=ChatSessionOut)
def update_chat_session(
    chat_id: str,
    payload: ChatSessionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    chat = _get_chat_session(db, chat_id, user)
    if payload.title is not None:
        chat.title = payload.title
    if "library_id" in payload.model_fields_set:
        if payload.library_id:
            _get_library(db, payload.library_id, user)
        chat.library_id = payload.library_id
    chat.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(chat)
    return _chat_session_out(chat)


@app.delete("/api/chats/{chat_id}", status_code=204, response_class=Response)
def delete_chat_session(chat_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    db.delete(_get_chat_session(db, chat_id, user))
    db.commit()


@app.get("/api/chats/{chat_id}/messages", response_model=list[ChatMessageOut])
def list_chat_messages(chat_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _get_chat_session(db, chat_id, user)
    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.chat_session_id == chat_id)
        .order_by(ChatMessage.created_at.asc())
    ).all()
    return [_chat_message_out(message) for message in messages]


@app.post("/api/chats/{chat_id}/messages")
async def send_chat_message(
    chat_id: str,
    payload: ChatMessageCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    chat = _get_chat_session(db, chat_id, user)

    is_first_message = not db.scalar(
        select(func.count(ChatMessage.id)).where(ChatMessage.chat_session_id == chat.id)
    )
    user_message = ChatMessage(
        chat_session_id=chat.id,
        role="user",
        content=payload.query,
    )
    if is_first_message:
        chat.title = _chat_title_from_first_message(payload.query)
    chat.updated_at = datetime.now(timezone.utc)
    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    async def event_stream():
        try:
            matches = (
                await retrieve_chunks(db, payload.query, chat.library_id, None, payload.top_k)
                if chat.library_id
                else []
            )
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
            answer_parts: list[str] = []
            mode = "local"
            async for mode, content in stream_answer(payload.query, matches, get_runtime_settings(db)):
                answer_parts.append(content)
                yield _sse_event("delta", {"content": content})

            assistant_message = ChatMessage(
                chat_session_id=chat.id,
                role="assistant",
                content="".join(answer_parts),
                citations=[citation.model_dump() for citation in citations],
            )
            chat.updated_at = datetime.now(timezone.utc)
            db.add(assistant_message)
            db.commit()
            db.refresh(assistant_message)
            exchange = ChatExchangeOut(
                user_message=_chat_message_out(user_message),
                assistant_message=_chat_message_out(assistant_message),
                retrieval_mode=mode,
            )
            yield _sse_event("done", exchange.model_dump(mode="json"))
        except Exception as error:
            db.rollback()
            yield _sse_event("error", {"detail": f"Message generation failed: {error}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.library_id:
        _get_library(db, payload.library_id, user)
    matches = await retrieve_chunks(db, payload.query, payload.library_id, payload.chapter, payload.top_k)
    answer, mode = await generate_answer(payload.query, matches, get_runtime_settings(db))
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
    config = chunking or ChunkingConfig()
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


def _get_library(db: Session, library_id: str, user: User) -> Library:
    library = db.get(Library, library_id)
    if not library or (user.role != "super_admin" and library.owner_id != user.id):
        raise HTTPException(status_code=404, detail="Library not found")
    return library


def _ensure_document_progress_columns() -> None:
    column_names = {column["name"] for column in inspect(engine).get_columns("documents")}
    statements: list[str] = []
    if "processing_stage" not in column_names:
        statements.append(
            "ALTER TABLE documents ADD COLUMN processing_stage VARCHAR(32) NOT NULL DEFAULT 'queued'"
        )
    if "progress" not in column_names:
        statements.append("ALTER TABLE documents ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_ownership_columns() -> None:
    statements: list[str] = []
    library_columns = {column["name"] for column in inspect(engine).get_columns("libraries")}
    chat_columns = {column["name"] for column in inspect(engine).get_columns("chat_sessions")}
    if "owner_id" not in library_columns:
        statements.append("ALTER TABLE libraries ADD COLUMN owner_id VARCHAR(36)")
    if "user_id" not in chat_columns:
        statements.append("ALTER TABLE chat_sessions ADD COLUMN user_id VARCHAR(36)")
    if statements:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))


def _bootstrap_admin() -> None:
    db = SessionLocal()
    try:
        admin = db.scalar(select(User).where(User.role == "super_admin").order_by(User.created_at.asc()))
        if not admin and not db.scalar(select(func.count(User.id))):
            username = (settings.bootstrap_admin_username or "").strip()
            password = settings.bootstrap_admin_password or ""
            if username and password:
                admin = User(
                    username=username,
                    display_name=username,
                    password_hash=hash_password(password),
                    role="super_admin",
                )
                db.add(admin)
                db.commit()
                db.refresh(admin)
        if admin:
            db.execute(update(Library).where(Library.owner_id.is_(None)).values(owner_id=admin.id))
            db.execute(update(ChatSession).where(ChatSession.user_id.is_(None)).values(user_id=admin.id))
            db.commit()
    finally:
        db.close()


async def _ingest_document(
    document_id: str,
    payload: bytes,
    suffix: str,
    chunking: ChunkingConfig,
) -> None:
    db = SessionLocal()
    try:
        document = db.get(Document, document_id)
        if not document:
            return

        _set_document_progress(db, document, "extracting", 5)
        raw_text, page_count = extract_text_from_bytes(payload, suffix)
        document.raw_text = raw_text
        document.page_count = page_count
        _set_document_progress(db, document, "parsing", 25)

        parsed_questions = parse_questions(raw_text)
        for parsed in parsed_questions:
            db.add(
                Question(
                    library_id=document.library_id,
                    document_id=document.id,
                    sequence=parsed.sequence,
                    stem=parsed.stem,
                    options=parsed.options,
                    answer=parsed.answer,
                    analysis=parsed.analysis,
                    source_page=parsed.source_page,
                )
            )
        _set_document_progress(db, document, "chunking", 45)

        chunks = _create_chunks(document, raw_text, chunking)
        db.add_all(chunks)
        db.flush()
        _set_document_progress(db, document, "indexing", 70)
        await index_chunks(db, chunks)

        document.status = "ready"
        document.processing_stage = "ready"
        document.progress = 100
        document.error_message = None
        db.commit()
    except Exception as error:
        db.rollback()
        document = db.get(Document, document_id)
        if document:
            document.status = "failed"
            document.processing_stage = "failed"
            document.error_message = f"{type(error).__name__}: {error}"
            db.commit()
    finally:
        db.close()


def _set_document_progress(db: Session, document: Document, stage: str, progress: int) -> None:
    document.status = "processing"
    document.processing_stage = stage
    document.progress = progress
    document.error_message = None
    db.commit()


def _get_chat_session(db: Session, chat_id: str, user: User) -> ChatSession:
    chat = db.scalar(
        select(ChatSession)
        .options(joinedload(ChatSession.library))
        .where(ChatSession.id == chat_id)
    )
    if not chat or (user.role != "super_admin" and chat.user_id != user.id):
        raise HTTPException(status_code=404, detail="Chat not found")
    return chat


def _library_statement(user: User):
    statement = select(Library)
    return statement if user.role == "super_admin" else statement.where(Library.owner_id == user.id)


def _chat_owner_filters(user: User):
    return () if user.role == "super_admin" else (ChatSession.user_id == user.id,)


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _active_super_admin_count(db: Session) -> int:
    return db.scalar(
        select(func.count(User.id)).where(User.role == "super_admin", User.is_active.is_(True))
    ) or 0


def _audit(
    db: Session,
    actor: User,
    action: str,
    target_type: str,
    target_id: str | None,
    detail: dict | None = None,
) -> None:
    db.add(AuditLog(actor_id=actor.id, action=action, target_type=target_type, target_id=target_id, detail=detail))
    db.commit()


def _chat_title_from_first_message(query: str) -> str:
    title = " ".join(query.split()).rstrip("。！？?!；;，,")
    if not title:
        return "新建聊天"
    return f"{title[:36]}..." if len(title) > 36 else title


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
        processing_stage=document.processing_stage,
        progress=document.progress,
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


def _document_chunk_summary_out(chunk: DocumentChunk) -> DocumentChunkSummaryOut:
    content = " ".join(chunk.content.split())
    preview_length = 180
    return DocumentChunkSummaryOut(
        id=chunk.id,
        sequence=chunk.sequence,
        content_preview=f"{content[:preview_length]}..." if len(content) > preview_length else content,
        chapter=chunk.chapter,
        source_page_start=chunk.source_page_start,
        source_page_end=chunk.source_page_end,
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


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
