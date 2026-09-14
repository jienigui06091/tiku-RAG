# Tiku RAG

Tiku RAG imports question banks and reference documents into a standard RAG
pipeline. PostgreSQL holds metadata, parsed questions, and retrievable document
chunks. Original files are stored in MinIO, and Milvus stores chunk embeddings.

## Ingestion pipeline

1. The uploaded original is written to MinIO.
2. PDF, DOCX, TXT, and Markdown content is extracted in memory.
3. Structured question documents are additionally parsed into individual
   question records for the question-bank view.
4. Every document is split into chunks by page and section. Chunks use a
   configurable character window with overlap and retain document, section,
   page, and character-range metadata.
5. The chunk content is embedded and indexed in Milvus.
6. Chat retrieval returns source chunks with document, section, and page
   citations.

Existing documents can be converted to chunks with:

```text
POST /api/libraries/{library_id}/reindex
```

Use `http://localhost:8000/docs` to run this endpoint after starting the
backend. The new `document_chunks` table is created automatically at startup.

## Configuration

Copy `.env.example` to `.env`. Database connection, CORS, cookie settings,
the configuration-encryption key, and bootstrap administrator remain deployment
settings in the environment file:

```env
DATABASE_URL=postgresql+psycopg://username:password@db.example.com:5432/tiku_rag
SETTINGS_ENCRYPTION_KEY=<Fernet URL-safe base64 key>
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=change-me-to-a-long-password
SESSION_COOKIE_SECURE=true
```

On first startup, the bootstrap administrator is created from those two
administrator variables. Public registration is not available. Sign in with
that account to create members and configure the LLM, embedding, rerank,
Milvus, object storage, OCR, chunk defaults, and upload limits from the system
configuration page. Secret configuration values are encrypted before being
stored in the database and are never returned to the browser.

Generate a Fernet key with:

```powershell
cd backend
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

For standard deployment, do not use local object storage; it writes original
uploads beneath `backend/data/uploads`.

## Local startup

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

In another terminal:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Open `http://localhost:5173`.
