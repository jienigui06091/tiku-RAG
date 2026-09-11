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

Copy `.env.example` to `.env` and set the PostgreSQL, MinIO, Milvus, and
embedding-provider values.

```env
STORAGE_PROVIDER=minio
VECTOR_PROVIDER=milvus
MILVUS_COLLECTION=tiku_document_chunks
CHUNK_SIZE=800
CHUNK_OVERLAP=120
EMBEDDING_BATCH_SIZE=8
EMBEDDING_TIMEOUT_SECONDS=120
```

For standard deployment, do not use `STORAGE_PROVIDER=local`; it writes
original uploads under `backend/data/uploads`.

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
