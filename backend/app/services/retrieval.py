import re
from collections import Counter

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.models import DocumentChunk

settings = get_settings()


def _tokens(value: str) -> list[str]:
    normalized = re.sub(r"\s+", "", value.lower())
    chinese_bigrams = [normalized[index:index + 2] for index in range(len(normalized) - 1)]
    terms = re.findall(r"[a-z0-9_]+", normalized)
    return chinese_bigrams + terms


def _lexical_score(query: str, candidate: str) -> float:
    query_terms = Counter(_tokens(query))
    candidate_terms = Counter(_tokens(candidate))
    if not query_terms or not candidate_terms:
        return 0.0
    overlap = sum(min(count, candidate_terms[term]) for term, count in query_terms.items())
    return overlap / max(len(query_terms), 1)


async def retrieve_chunks(
    db: Session,
    query: str,
    library_id: str | None,
    chapter: str | None,
    limit: int,
) -> list[tuple[DocumentChunk, float]]:
    if settings.vector_provider.lower() == "milvus":
        try:
            return await _milvus_retrieve(db, query, library_id, chapter, limit)
        except (httpx.HTTPError, ValueError, KeyError, ImportError):
            # The lexical fallback keeps the question-answer workflow available.
            pass

    statement = select(DocumentChunk).options(joinedload(DocumentChunk.document))
    if library_id:
        statement = statement.where(DocumentChunk.library_id == library_id)
    if chapter:
        statement = statement.where(DocumentChunk.chapter == chapter)

    chunks = db.scalars(statement).unique().all()
    ranked = [(chunk, _lexical_score(query, chunk.content)) for chunk in chunks]
    return sorted(ranked, key=lambda item: item[1], reverse=True)[:limit]


async def index_chunks(chunks: list[DocumentChunk]) -> None:
    if settings.vector_provider.lower() != "milvus" or not chunks:
        return
    if not (settings.milvus_uri and settings.embedding_base_url and settings.embedding_model):
        raise ValueError("Milvus retrieval requires MILVUS_URI, EMBEDDING_BASE_URL, and EMBEDDING_MODEL")

    vectors = await _embed([chunk.content for chunk in chunks])
    if not vectors:
        return

    client = _milvus_client()
    if not client.has_collection(collection_name=settings.milvus_collection):
        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="chunk_id", datatype=_milvus_data_type("VARCHAR"), is_primary=True, max_length=36)
        schema.add_field(field_name="library_id", datatype=_milvus_data_type("VARCHAR"), max_length=36)
        schema.add_field(field_name="document_id", datatype=_milvus_data_type("VARCHAR"), max_length=36)
        schema.add_field(
            field_name="vector",
            datatype=_milvus_data_type("FLOAT_VECTOR"),
            dim=len(vectors[0]),
        )
        index_params = client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        client.create_collection(
            collection_name=settings.milvus_collection,
            schema=schema,
            index_params=index_params,
        )

    chunk_ids = [chunk.id for chunk in chunks]
    _delete_ids(client, chunk_ids)
    client.insert(
        collection_name=settings.milvus_collection,
        data=[
            {
                "chunk_id": chunk.id,
                "library_id": chunk.library_id,
                "document_id": chunk.document_id,
                "vector": vector,
            }
            for chunk, vector in zip(chunks, vectors, strict=True)
        ],
    )
    client.flush(collection_name=settings.milvus_collection)


async def delete_chunks(document_ids: list[str]) -> None:
    if settings.vector_provider.lower() != "milvus" or not document_ids:
        return
    client = _milvus_client()
    if not client.has_collection(collection_name=settings.milvus_collection):
        return
    quoted_ids = ",".join(f'"{item}"' for item in document_ids)
    client.delete(
        collection_name=settings.milvus_collection,
        filter=f"document_id in [{quoted_ids}]",
    )
    client.flush(collection_name=settings.milvus_collection)


async def _milvus_retrieve(
    db: Session,
    query: str,
    library_id: str | None,
    chapter: str | None,
    limit: int,
) -> list[tuple[DocumentChunk, float]]:
    if not (settings.milvus_uri and settings.embedding_base_url and settings.embedding_model):
        raise ValueError("Milvus retrieval is not configured")

    vector = (await _embed([query]))[0]
    client = _milvus_client()
    filters: list[str] = []
    if library_id:
        filters.append(f'library_id == "{library_id}"')
    result = client.search(
        collection_name=settings.milvus_collection,
        data=[vector],
        limit=limit,
        output_fields=["chunk_id"],
        filter=" and ".join(filters) or None,
    )
    hits = result[0] if result else []
    ids = [hit["entity"]["chunk_id"] for hit in hits]
    if not ids:
        return []

    chunks = db.scalars(
        select(DocumentChunk)
        .options(joinedload(DocumentChunk.document))
        .where(DocumentChunk.id.in_(ids))
    ).unique().all()
    by_id = {chunk.id: chunk for chunk in chunks}
    matches = [
        (by_id[hit["entity"]["chunk_id"]], float(hit["distance"]))
        for hit in hits
        if hit["entity"]["chunk_id"] in by_id
    ]
    if chapter:
        matches = [(chunk, score) for chunk, score in matches if chunk.chapter == chapter]
    return matches


def _milvus_client():
    try:
        from pymilvus import MilvusClient
    except ImportError as error:
        raise ImportError("pymilvus is required when VECTOR_PROVIDER=milvus") from error
    return MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token)


def _milvus_data_type(name: str):
    try:
        from pymilvus import DataType
    except ImportError as error:
        raise ImportError("pymilvus is required when VECTOR_PROVIDER=milvus") from error
    return getattr(DataType, name)


def _delete_ids(client, chunk_ids: list[str]) -> None:
    quoted_ids = ",".join(f'"{item}"' for item in chunk_ids)
    client.delete(
        collection_name=settings.milvus_collection,
        filter=f"chunk_id in [{quoted_ids}]",
    )


async def _embed(texts: list[str]) -> list[list[float]]:
    if not settings.embedding_base_url:
        raise ValueError("EMBEDDING_BASE_URL is required for vector retrieval")
    if settings.embedding_batch_size < 1:
        raise ValueError("EMBEDDING_BATCH_SIZE must be at least 1")

    headers = {"Authorization": f"Bearer {settings.embedding_api_key}"} if settings.embedding_api_key else {}
    vectors: list[list[float]] = []
    async with httpx.AsyncClient(timeout=settings.embedding_timeout_seconds, trust_env=False) as client:
        for start in range(0, len(texts), settings.embedding_batch_size):
            batch = texts[start:start + settings.embedding_batch_size]
            response = await client.post(
                f"{settings.embedding_base_url.rstrip('/')}/embeddings",
                headers=headers,
                json={"model": settings.embedding_model, "input": batch},
            )
            response.raise_for_status()
            data = response.json()["data"]
            vectors.extend(item["embedding"] for item in sorted(data, key=lambda item: item["index"]))
    return vectors


async def generate_answer(query: str, matches: list[tuple[DocumentChunk, float]]) -> tuple[str, str]:
    if not matches:
        return "No relevant source content was found in the selected library.", "local"

    best_chunk, _ = matches[0]
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        return best_chunk.content, "local"

    context = "\n\n".join(
        _format_context(index, chunk)
        for index, (chunk, _) in enumerate(matches, start=1)
    )
    payload = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Answer only from the supplied source chunks. "
                    "State when the sources do not provide enough evidence."
                ),
            },
            {"role": "user", "content": f"Question: {query}\n\nSource chunks:\n{context}"},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json=payload,
            )
            response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"].strip()
        return answer, "llm"
    except (httpx.HTTPError, KeyError, IndexError, TypeError):
        return best_chunk.content, "local-fallback"


def _format_context(index: int, chunk: DocumentChunk) -> str:
    source = chunk.document.filename if chunk.document else "Unknown document"
    page = ""
    if chunk.source_page_start:
        page = f", page {chunk.source_page_start}"
        if chunk.source_page_end and chunk.source_page_end != chunk.source_page_start:
            page += f"-{chunk.source_page_end}"
    section = f", section {chunk.chapter}" if chunk.chapter else ""
    return f"[{index}] {source}{page}{section}\n{chunk.content}"
