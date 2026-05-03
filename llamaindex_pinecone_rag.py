"""
Reference Vault LlamaIndex + Pinecone RAG service.

SQLite remains the source of truth. Pinecone stores vectors and routing
metadata only. This module only accepts Reference Vault documents and returns
SQLite-verified chunks for downstream Workbench/Chat use.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import database
from tls_config import sanitize_tls_ca_bundle_env


REFERENCE_DOCUMENT_SOURCE_TYPE = "reference_document"
INDEX_STATUS_INDEXED = "indexed"
INDEX_STATUS_FAILED = "failed"
INDEX_STATUS_PENDING = "pending"
EMBEDDING_MODEL_NAME = "pinecone:multilingual-e5-large"


@dataclass
class RetrievedDocument:
    """Minimal LangChain-compatible document shape used by existing app code."""

    page_content: str
    metadata: dict[str, Any]


def _json_dict(value) -> dict:
    if not value:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def _chunk_id(document_id: str, chunk_index: int, content_hash: str) -> str:
    raw = f"{document_id}:{chunk_index}:{content_hash}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def _merge_metadata(document: dict, updates: dict) -> dict:
    metadata = _json_dict(document.get("metadata_json"))
    metadata.update(updates)
    return metadata


def _mark_index_status(document: dict, status: str, extra: dict | None = None) -> None:
    payload = {"index_status": status}
    if extra:
        payload.update(extra)
    database.update_reference_vault_document_metadata(
        document["document_id"],
        metadata_json=_merge_metadata(document, payload),
        user_id=document["user_id"],
        project_id=document["project_id"],
    )


def _require_indexable_document(
    document_id: str,
    user_id: str,
    project_id: str | None = None,
) -> dict:
    document = database.get_reference_vault_document(
        document_id,
        user_id=user_id,
        project_id=project_id,
    )
    if not document:
        raise ValueError("Reference Vault document not found")
    if document.get("source_type") != REFERENCE_DOCUMENT_SOURCE_TYPE:
        raise ValueError("Only Reference Vault documents can be indexed")
    if document.get("status") != "active":
        raise ValueError("Only active Reference Vault documents can be indexed")
    if document.get("docling_status") != "succeeded":
        raise ValueError("Only successfully converted Docling documents can be indexed")
    if not document.get("storage_path") or not Path(document["storage_path"]).exists():
        raise ValueError("Original uploaded PDF artifact is required for indexing")
    markdown_path = document.get("docling_markdown_path")
    if not markdown_path or not Path(markdown_path).exists():
        raise ValueError("Docling Markdown artifact is required for indexing")
    return document


def _node_text(node) -> str:
    if hasattr(node, "get_content"):
        try:
            return node.get_content(metadata_mode="none")
        except TypeError:
            return node.get_content()
    return getattr(node, "text", "") or getattr(node, "page_content", "") or ""


def _set_node_id(node, node_id: str) -> None:
    if hasattr(node, "id_"):
        node.id_ = node_id
    elif hasattr(node, "node_id"):
        node.node_id = node_id


def _node_id(node) -> str | None:
    return getattr(node, "node_id", None) or getattr(node, "id_", None)


def _set_node_metadata(node, metadata: dict) -> None:
    existing = dict(getattr(node, "metadata", {}) or {})
    existing.update(metadata)
    node.metadata = existing


def _build_nodes_from_markdown(document: dict) -> list:
    from llama_index.core import Document as LlamaDocument
    from llama_index.core.node_parser import SentenceSplitter

    markdown_path = Path(document["docling_markdown_path"])
    markdown_text = markdown_path.read_text(encoding="utf-8")
    base_metadata = {
        "user_id": document["user_id"],
        "project_id": document["project_id"],
        "document_id": document["document_id"],
        "source_type": REFERENCE_DOCUMENT_SOURCE_TYPE,
        "paper_name": document.get("paper_name") or document.get("filename") or "",
        "author_display": document.get("author_display") or "",
        "publication_year": document.get("publication_year") or "",
        "doi": document.get("doi") or "",
        "openalex_id": document.get("openalex_id") or "",
        "docling_markdown_path": str(markdown_path),
    }
    llama_doc = LlamaDocument(text=markdown_text, metadata=base_metadata)
    splitter = SentenceSplitter(chunk_size=1024, chunk_overlap=128)
    return splitter.get_nodes_from_documents([llama_doc])


def _chunk_records_from_nodes(document: dict, nodes: Iterable) -> tuple[list[dict], list]:
    records = []
    prepared_nodes = []
    for chunk_index, node in enumerate(nodes):
        content = _node_text(node).strip()
        if not content:
            continue
        content_hash = _content_hash(content)
        chunk_id = _chunk_id(document["document_id"], chunk_index, content_hash)
        metadata = {
            "user_id": document["user_id"],
            "project_id": document["project_id"],
            "document_id": document["document_id"],
            "chunk_id": chunk_id,
            "source_type": REFERENCE_DOCUMENT_SOURCE_TYPE,
            "paper_name": document.get("paper_name") or document.get("filename") or "",
            "author_display": document.get("author_display") or "",
            "publication_year": document.get("publication_year") or "",
            "doi": document.get("doi") or "",
            "openalex_id": document.get("openalex_id") or "",
            "chunk_index": chunk_index,
            "content_hash": content_hash,
            "docling_markdown_path": document.get("docling_markdown_path") or "",
        }
        _set_node_id(node, chunk_id)
        _set_node_metadata(node, metadata)
        records.append({
            "chunk_id": chunk_id,
            "document_id": document["document_id"],
            "user_id": document["user_id"],
            "project_id": document["project_id"],
            "chunk_index": chunk_index,
            "markdown_content": content,
            "content": content,
            "content_hash": content_hash,
            "pinecone_vector_id": None,
            "embedding_model": EMBEDDING_MODEL_NAME,
            "source_type": REFERENCE_DOCUMENT_SOURCE_TYPE,
            "metadata_json": metadata,
        })
        prepared_nodes.append(node)
    if not records:
        raise ValueError("No non-empty chunks were produced from Docling Markdown")
    return records, prepared_nodes


def _pinecone_embedding_model():
    sanitize_tls_ca_bundle_env()
    from pydantic import PrivateAttr
    from llama_index.core.embeddings import BaseEmbedding
    from vector_store import _embed_query, _embed_texts, get_embedding_model

    class PineconeInferenceEmbedding(BaseEmbedding):
        _client: Any = PrivateAttr()

        def __init__(self, pc_client, **kwargs):
            super().__init__(model_name=EMBEDDING_MODEL_NAME, **kwargs)
            self._client = pc_client

        @classmethod
        def class_name(cls) -> str:
            return "PineconeInferenceEmbedding"

        def _get_query_embedding(self, query: str) -> list[float]:
            return _embed_query(self._client, query)

        async def _aget_query_embedding(self, query: str) -> list[float]:
            return self._get_query_embedding(query)

        def _get_text_embedding(self, text: str) -> list[float]:
            return _embed_texts(self._client, [text])[0]

        def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
            return _embed_texts(self._client, texts)

    return PineconeInferenceEmbedding(get_embedding_model())


def _pinecone_vector_store(user_id: str):
    sanitize_tls_ca_bundle_env()
    from llama_index.vector_stores.pinecone import PineconeVectorStore
    from vector_store import get_pinecone_index

    return PineconeVectorStore(
        pinecone_index=get_pinecone_index(),
        namespace=user_id,
    )


def _index_nodes_with_llamaindex(nodes: list, user_id: str) -> list[str]:
    from llama_index.core import StorageContext, VectorStoreIndex

    vector_store = _pinecone_vector_store(user_id)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    VectorStoreIndex(
        nodes,
        storage_context=storage_context,
        embed_model=_pinecone_embedding_model(),
    )
    return [_node_id(node) for node in nodes]


def _delete_existing_vectors(document: dict) -> None:
    from vector_store import get_pinecone_index

    old_chunks = database.get_reference_vault_chunks_by_document(
        document["document_id"],
        user_id=document["user_id"],
        project_id=document["project_id"],
    )
    vector_ids = [
        chunk.get("pinecone_vector_id")
        for chunk in old_chunks
        if chunk.get("pinecone_vector_id") and chunk.get("user_id") == document["user_id"]
    ]
    if vector_ids:
        get_pinecone_index().delete(ids=vector_ids, namespace=document["user_id"])


def index_reference_vault_document(
    document_id: str,
    user_id: str,
    project_id: str | None = None,
) -> dict:
    """Index one active Docling-backed Reference Vault document."""
    document = _require_indexable_document(document_id, user_id, project_id)
    project_id = project_id or document["project_id"]
    try:
        nodes = _build_nodes_from_markdown(document)
        records, prepared_nodes = _chunk_records_from_nodes(document, nodes)
        try:
            _delete_existing_vectors(document)
        except Exception:
            pass
        database.delete_reference_vault_chunks_by_document(document_id, user_id, project_id)
        database.save_reference_vault_chunks_batch(records)
        vector_ids = _index_nodes_with_llamaindex(prepared_nodes, user_id)
        for vector_id in vector_ids:
            if vector_id:
                database.update_reference_vault_chunk_vector_id(
                    vector_id,
                    vector_id,
                    user_id=user_id,
                    project_id=project_id,
                )
        _mark_index_status(
            document,
            INDEX_STATUS_INDEXED,
            {
                "chunk_count": len(records),
                "embedding_model": EMBEDDING_MODEL_NAME,
            },
        )
        return {
            "document_id": document_id,
            "user_id": user_id,
            "project_id": project_id,
            "chunk_count": len(records),
            "vector_count": len([vid for vid in vector_ids if vid]),
            "index_status": INDEX_STATUS_INDEXED,
        }
    except Exception as exc:
        _mark_index_status(document, INDEX_STATUS_FAILED, {"index_error": str(exc)})
        raise


def index_docling_result(docling_result) -> dict:
    """Index a successful Docling ingestion result."""
    return index_reference_vault_document(
        document_id=docling_result.document_id,
        user_id=docling_result.user_id,
        project_id=docling_result.project_id,
    )


def _metadata_filters(user_id: str, project_id: str | None = None):
    from llama_index.core.vector_stores import ExactMatchFilter, MetadataFilters

    filters = [
        ExactMatchFilter(key="user_id", value=user_id),
        ExactMatchFilter(key="source_type", value=REFERENCE_DOCUMENT_SOURCE_TYPE),
    ]
    if project_id is not None:
        filters.append(ExactMatchFilter(key="project_id", value=project_id))
    return MetadataFilters(filters=filters)


def _retrieve_nodes_with_llamaindex(
    query: str,
    user_id: str,
    project_id: str | None,
    k: int,
) -> list:
    from llama_index.core import VectorStoreIndex

    vector_store = _pinecone_vector_store(user_id)
    index = VectorStoreIndex.from_vector_store(
        vector_store,
        embed_model=_pinecone_embedding_model(),
    )
    retriever = index.as_retriever(
        similarity_top_k=k,
        filters=_metadata_filters(user_id, project_id),
    )
    return retriever.retrieve(query)


def _candidate_ids(candidate) -> tuple[str | None, str | None, dict]:
    node = getattr(candidate, "node", candidate)
    metadata = dict(getattr(node, "metadata", {}) or {})
    node_id = _node_id(node)
    return metadata.get("pinecone_vector_id") or node_id, metadata.get("chunk_id") or node_id, metadata


def _verified_documents_from_candidates(
    candidates: list,
    user_id: str,
    project_id: str | None = None,
    document_ids: list[str] | None = None,
) -> list[RetrievedDocument]:
    vector_ids = []
    chunk_ids = []
    for candidate in candidates:
        vector_id, chunk_id, _metadata = _candidate_ids(candidate)
        if vector_id:
            vector_ids.append(vector_id)
        if chunk_id:
            chunk_ids.append(chunk_id)

    chunks = database.get_reference_vault_chunks_by_vector_ids(
        vector_ids,
        user_id=user_id,
        project_id=project_id,
    )
    by_chunk_id = {
        chunk["chunk_id"]: chunk
        for chunk in database.get_reference_vault_chunks_by_ids(
            chunk_ids,
            user_id=user_id,
            project_id=project_id,
        )
    }
    by_vector_id = {chunk.get("pinecone_vector_id"): chunk for chunk in chunks}
    allowed_document_ids = set(document_ids or [])

    verified = []
    seen = set()
    for candidate in candidates:
        vector_id, chunk_id, _metadata = _candidate_ids(candidate)
        chunk = by_vector_id.get(vector_id) or by_chunk_id.get(chunk_id)
        if not chunk or chunk["chunk_id"] in seen:
            continue
        if chunk.get("source_type") != REFERENCE_DOCUMENT_SOURCE_TYPE:
            continue
        if allowed_document_ids and chunk.get("document_id") not in allowed_document_ids:
            continue
        document = database.get_reference_vault_document(
            chunk["document_id"],
            user_id=user_id,
            project_id=chunk.get("project_id"),
        )
        if not _is_retrievable_document(document):
            continue
        seen.add(chunk["chunk_id"])
        verified.append(RetrievedDocument(
            page_content=chunk.get("content") or chunk.get("markdown_content") or "",
            metadata={
                "source_type": REFERENCE_DOCUMENT_SOURCE_TYPE,
                "user_id": chunk["user_id"],
                "project_id": chunk["project_id"],
                "document_id": chunk["document_id"],
                "chunk_id": chunk["chunk_id"],
                "pinecone_vector_id": chunk.get("pinecone_vector_id"),
                "paper_name": document.get("paper_name") or document.get("filename") or "",
                "filename": document.get("filename") or "",
                "author_display": document.get("author_display") or "",
                "publication_year": document.get("publication_year") or "",
                "doi": document.get("doi") or "",
                "openalex_id": document.get("openalex_id") or "",
                "chunk_index": chunk.get("chunk_index"),
                "section_title": chunk.get("section_title") or "",
            },
        ))
    return verified


def _is_retrievable_document(document: dict | None) -> bool:
    if not document:
        return False
    if document.get("source_type") != REFERENCE_DOCUMENT_SOURCE_TYPE:
        return False
    if document.get("status") != "active":
        return False
    if document.get("docling_status") != "succeeded":
        return False
    if not document.get("storage_path") or not document.get("docling_markdown_path"):
        return False
    return True


def retrieve_reference_vault(
    query: str,
    user_id: str,
    project_id: str | None = None,
    k: int = 5,
    document_ids: list[str] | None = None,
) -> list[RetrievedDocument]:
    """Retrieve SQLite-verified Reference Vault chunks using strict filters."""
    if not query or not user_id:
        return []
    candidates = _retrieve_nodes_with_llamaindex(query, user_id, project_id, k)
    if not candidates:
        return []
    return _verified_documents_from_candidates(
        candidates,
        user_id=user_id,
        project_id=project_id,
        document_ids=document_ids,
    )[:k]
