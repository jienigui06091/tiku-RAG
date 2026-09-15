import json
import re
from collections import Counter

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import DocumentChunk
from app.services.runtime_config import get_runtime_settings


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
    settings = get_runtime_settings(db)
    if settings.vector_provider.lower() == "milvus":
        try:
            matches = await _milvus_retrieve(db, query, library_id, chapter, limit, settings)
            return await _rerank(query, matches, limit, settings)
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
    matches = sorted(ranked, key=lambda item: item[1], reverse=True)[: max(limit * 4, limit)]
    return await _rerank(query, matches, limit, settings)


async def index_chunks(db: Session, chunks: list[DocumentChunk]) -> None:
    settings = get_runtime_settings(db)
    if settings.vector_provider.lower() != "milvus" or not chunks:
        return
    if not (settings.milvus_uri and settings.embedding_base_url and settings.embedding_model):
        raise ValueError("Milvus retrieval requires MILVUS_URI, EMBEDDING_BASE_URL, and EMBEDDING_MODEL")

    vectors = await _embed([chunk.content for chunk in chunks], settings)
    if not vectors:
        return

    client = _milvus_client(settings)
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


async def delete_chunks(db: Session, document_ids: list[str]) -> None:
    settings = get_runtime_settings(db)
    if settings.vector_provider.lower() != "milvus" or not document_ids:
        return
    client = _milvus_client(settings)
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
    settings,
) -> list[tuple[DocumentChunk, float]]:
    if not (settings.milvus_uri and settings.embedding_base_url and settings.embedding_model):
        raise ValueError("Milvus retrieval is not configured")

    vector = (await _embed([query], settings))[0]
    client = _milvus_client(settings)
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


def _milvus_client(settings):
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


async def _embed(texts: list[str], settings) -> list[list[float]]:
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


async def _rerank(
    query: str,
    matches: list[tuple[DocumentChunk, float]],
    limit: int,
    settings,
) -> list[tuple[DocumentChunk, float]]:
    if not matches or not (settings.rerank_base_url and settings.rerank_model):
        return matches[:limit]

    headers = {"Authorization": f"Bearer {settings.rerank_api_key}"} if settings.rerank_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=settings.embedding_timeout_seconds, trust_env=False) as client:
            response = await client.post(
                f"{settings.rerank_base_url.rstrip('/')}/rerank",
                headers=headers,
                json={
                    "model": settings.rerank_model,
                    "query": query,
                    "documents": [chunk.content for chunk, _ in matches],
                    "top_n": limit,
                },
            )
            response.raise_for_status()
        results = response.json()["results"]
        reranked: list[tuple[DocumentChunk, float]] = []
        for item in results:
            index = int(item["index"])
            if 0 <= index < len(matches):
                reranked.append((matches[index][0], float(item.get("relevance_score", item.get("score", 0)))))
        return reranked[:limit] or matches[:limit]
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return matches[:limit]


async def generate_answer(query: str, matches: list[tuple[DocumentChunk, float]], settings) -> tuple[str, str]:
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        if not matches:
            return "No LLM model is configured. Attach a knowledge base or configure an LLM before sending a message.", "local"
        best_chunk, _ = matches[0]
        return best_chunk.content, "local"

    payload = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "messages": _answer_messages(query, matches),
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
        if not matches:
            return "The LLM request failed. Please check the LLM configuration and try again.", "local-fallback"
        best_chunk, _ = matches[0]
        return best_chunk.content, "local-fallback"


async def stream_answer(query: str, matches: list[tuple[DocumentChunk, float]], settings=None):
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        if not matches:
            async for item in _stream_text(
                "No LLM model is configured. Attach a knowledge base or configure an LLM before sending a message.",
                "local",
            ):
                yield item
            return
        best_chunk, _ = matches[0]
        async for item in _stream_text(best_chunk.content, "local"):
            yield item
        return

    payload = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "stream": True,
        "messages": _answer_messages(query, matches),
    }
    received_content = False
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            async with client.stream(
                "POST",
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json=payload,
            ) as response:
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        return
                    event = json.loads(data)
                    content = event["choices"][0].get("delta", {}).get("content")
                    if content:
                        received_content = True
                        yield "llm", content
    except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError):
        if received_content:
            return

    if not received_content:
        if not matches:
            async for item in _stream_text(
                "The LLM request failed. Please check the LLM configuration and try again.",
                "local-fallback",
            ):
                yield item
            return
        best_chunk, _ = matches[0]
        async for item in _stream_text(best_chunk.content, "local-fallback"):
            yield item


async def _stream_text(content: str, mode: str, chunk_size: int = 80):
    for start in range(0, len(content), chunk_size):
        yield mode, content[start:start + chunk_size]


def _format_context(index: int, chunk: DocumentChunk) -> str:
    source = chunk.document.filename if chunk.document else "Unknown document"
    page = ""
    if chunk.source_page_start:
        page = f", page {chunk.source_page_start}"
        if chunk.source_page_end and chunk.source_page_end != chunk.source_page_start:
            page += f"-{chunk.source_page_end}"
    section = f", section {chunk.chapter}" if chunk.chapter else ""
    return f"[{index}] {source}{page}{section}\n{chunk.content}"


def _answer_messages(query: str, matches: list[tuple[DocumentChunk, float]]) -> list[dict[str, str]]:
    if not matches:
        return [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": query},
        ]

    context = "\n\n".join(
        _format_context(index, chunk)
        for index, (chunk, _) in enumerate(matches, start=1)
    )
    return [
        {
            "role": "system",
            "content": (
                "Answer only from the supplied source chunks. "
                "State when the sources do not provide enough evidence."
            ),
        },
        {"role": "user", "content": f"Question: {query}\n\nSource chunks:\n{context}"},
    ]
