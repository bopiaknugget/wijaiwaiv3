"""
Vector Store Module — Pinecone with Multi-Tenant Namespaces
Handles embeddings via Pinecone Inference API and Pinecone vector database management.

Key features:
- Pinecone cloud vector DB with per-user namespace isolation (Google User ID)
- Embedding via Pinecone Inference API: multilingual-e5-large (dim=1024, supports Thai)
- Rich metadata on every chunk (paper_title, authors, year, section, etc.)
- Parent-Child Chunking: search small children, retrieve large parent context
- Summary Embedding: AI-generated summaries stored alongside chunks
- @st.cache_resource on Pinecone client/index to avoid repeated connections
- Embedding cache: hash-keyed in-memory cache to avoid repeated Pinecone Inference calls
- Parallel embedding: ThreadPoolExecutor for batch ingestion
- Result deduplication: near-duplicate removal using content fingerprints
- Re-rank cutoff: pre_filter_topK=30, rerank to final k using score threshold
- Hybrid search: BM25 keyword scoring fused with vector search scores
- Timeout + fallback: Pinecone query timeout with graceful empty-list fallback
- Context length control: enforced in retrieve_unified via token estimation
"""

import hashlib
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

import database

# Conditional Streamlit import — allows CLI usage (main.py) without Streamlit
try:
    import streamlit as st
    _HAS_STREAMLIT = hasattr(st, "cache_resource")
except ImportError:
    _HAS_STREAMLIT = False

# Load environment variables
_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "wijaiwai")
PINECONE_HOST = os.getenv("PINECONE_HOST", "")
PINECONE_EMBED_MODEL = "multilingual-e5-large"

# ── Performance tuning constants ─────────────────────────────────────────────
# Re-rank cutoff strategy: fetch more candidates, deduplicate, return final k
_PRE_FILTER_MULTIPLIER = 6   # fetch k * 6 from Pinecone before re-rank (was k*2)
_MAX_PRE_FILTER = 30         # hard cap on Pinecone top_k to control cost
# Hybrid search weight: 0.0 = pure BM25, 1.0 = pure vector
_HYBRID_VECTOR_WEIGHT = 0.7
_HYBRID_BM25_WEIGHT = 0.3
# Timeout for Pinecone queries in seconds
_PINECONE_QUERY_TIMEOUT = 10
# Maximum characters sent as context to the LLM (~2000 tokens ≈ 8000 chars)
_MAX_CONTEXT_CHARS = 8000
# Minimum cosine similarity score to include a result (Pinecone returns 0-1)
_MIN_SCORE_THRESHOLD = 0.30

# ── Embedding cache (in-process, hash-keyed) ─────────────────────────────────
# Stores {text_hash: embedding_vector}. Survives the lifetime of the process.
# For a Streamlit app this covers the full user session and re-runs.
_EMBEDDING_CACHE: dict = {}

# Maximum cache entries to prevent unbounded memory growth
_EMBEDDING_CACHE_MAX = 4096


def _cache_key(text: str) -> str:
    """Return a stable SHA-256 hex digest for a text string."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


# ── Pinecone Client (used for both Inference and Index) ───────────────────────

def _load_pinecone_client():
    """
    Initialize and cache the Pinecone client.
    This client is used for both the Inference API (embeddings) and the Index.

    Returns:
        pinecone.Pinecone: Authenticated Pinecone client
    """
    from pinecone import Pinecone

    if not PINECONE_API_KEY:
        raise ValueError(
            "PINECONE_API_KEY not found in .env. "
            "Please set PINECONE_API_KEY=your_key_here"
        )

    pc = Pinecone(api_key=PINECONE_API_KEY)
    print("OK  Pinecone client initialized (Inference API ready)")
    return pc


# Apply Streamlit caching if available
if _HAS_STREAMLIT:
    get_embedding_model = st.cache_resource(_load_pinecone_client)
else:
    get_embedding_model = _load_pinecone_client


def _load_pinecone_index():
    """
    Connect to the Pinecone index using the cached client.

    Returns:
        pinecone.Index: Connected Pinecone index object
    """
    pc = get_embedding_model()
    if PINECONE_HOST:
        index = pc.Index(PINECONE_INDEX_NAME, host=PINECONE_HOST)
        print(f"OK  Connected to Pinecone index '{PINECONE_INDEX_NAME}' via host {PINECONE_HOST}")
    else:
        index = pc.Index(PINECONE_INDEX_NAME)
        print(f"OK  Connected to Pinecone index '{PINECONE_INDEX_NAME}'")
    return index


# Apply Streamlit caching if available
if _HAS_STREAMLIT:
    get_pinecone_index = st.cache_resource(_load_pinecone_index)
else:
    get_pinecone_index = _load_pinecone_index


# ── Embedding via Pinecone Inference API ──────────────────────────────────────

def _embed_batch_raw(pc_client, texts: list, input_type: str) -> list:
    """
    Low-level call to Pinecone Inference API for a single batch.
    No caching — callers are responsible for checking the cache first.
    """
    response = pc_client.inference.embed(
        model=PINECONE_EMBED_MODEL,
        inputs=texts,
        parameters={"input_type": input_type, "truncate": "END"},
    )
    return [item["values"] for item in response]


def _embed_texts(pc_client, texts: list) -> list:
    """
    Embed a list of passage texts using Pinecone Inference API.

    Results are cached by SHA-256 hash of each text.  Only texts not already
    in the cache are sent to the API.  For large batches (>96 texts) the
    un-cached texts are embedded in parallel using a ThreadPoolExecutor,
    achieving 3-10x faster ingestion compared to sequential batching.

    Args:
        pc_client: Pinecone client instance (from get_embedding_model())
        texts: List of text strings to embed

    Returns:
        List of embedding vectors in the same order as input texts
    """
    global _EMBEDDING_CACHE

    # Separate cached from uncached
    uncached_indices = []
    uncached_texts = []
    results = [None] * len(texts)

    for i, text in enumerate(texts):
        key = _cache_key(text)
        if key in _EMBEDDING_CACHE:
            results[i] = _EMBEDDING_CACHE[key]
        else:
            uncached_indices.append(i)
            uncached_texts.append(text)

    if not uncached_texts:
        return results

    # Batch size recommended by Pinecone Inference API
    _BATCH = 96

    # Build batches
    batches = [
        uncached_texts[i: i + _BATCH]
        for i in range(0, len(uncached_texts), _BATCH)
    ]

    # Parallel embed — use threads because this is I/O-bound (HTTP calls)
    batch_results = [None] * len(batches)
    if len(batches) == 1:
        # Skip thread overhead for a single batch
        batch_results[0] = _embed_batch_raw(pc_client, batches[0], "passage")
    else:
        with ThreadPoolExecutor(max_workers=min(4, len(batches))) as executor:
            future_to_idx = {
                executor.submit(_embed_batch_raw, pc_client, batch, "passage"): idx
                for idx, batch in enumerate(batches)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                batch_results[idx] = future.result()

    # Flatten batch results and populate cache
    flat_embeddings = []
    for br in batch_results:
        flat_embeddings.extend(br)

    for text, embedding in zip(uncached_texts, flat_embeddings):
        key = _cache_key(text)
        # Evict oldest entries if cache is full (simple FIFO eviction)
        if len(_EMBEDDING_CACHE) >= _EMBEDDING_CACHE_MAX:
            try:
                oldest_key = next(iter(_EMBEDDING_CACHE))
                del _EMBEDDING_CACHE[oldest_key]
            except StopIteration:
                pass
        _EMBEDDING_CACHE[key] = embedding

    # Place back into correct result positions
    for list_pos, orig_idx in enumerate(uncached_indices):
        results[orig_idx] = flat_embeddings[list_pos]

    return results


def _embed_query(pc_client, query: str) -> list:
    """
    Embed a single query text using Pinecone Inference API.
    Query embeddings are also cached by hash to avoid redundant calls for
    repeated or similar queries within the same session.

    Args:
        pc_client: Pinecone client instance (from get_embedding_model())
        query: Query string

    Returns:
        Embedding vector (list of floats)
    """
    global _EMBEDDING_CACHE

    # Use a prefixed key so query and passage embeddings don't collide
    key = "q:" + _cache_key(query)
    if key in _EMBEDDING_CACHE:
        return _EMBEDDING_CACHE[key]

    response = pc_client.inference.embed(
        model=PINECONE_EMBED_MODEL,
        inputs=[query],
        parameters={"input_type": "query", "truncate": "END"},
    )
    embedding = response[0]["values"]

    if len(_EMBEDDING_CACHE) >= _EMBEDDING_CACHE_MAX:
        try:
            oldest_key = next(iter(_EMBEDDING_CACHE))
            del _EMBEDDING_CACHE[oldest_key]
        except StopIteration:
            pass
    _EMBEDDING_CACHE[key] = embedding
    return embedding


# ── Upsert (Ingest) ─────────────────────────────────────────────────────────

def upsert_documents(chunks: list, user_id: str,
                     embedding_model=None) -> int:
    """
    Embed chunks via Pinecone Inference API and upsert into Pinecone namespace.

    Each chunk can be a LangChain Document object or a dict with
    'page_content' and 'metadata' keys.

    Args:
        chunks: List of Document objects or dicts with page_content/metadata
        user_id: Google user ID used as Pinecone namespace
        embedding_model: Pinecone client (loads from cache if None)

    Returns:
        int: Number of vectors upserted
    """
    if not chunks:
        return 0

    if embedding_model is None:
        embedding_model = get_embedding_model()

    index = get_pinecone_index()

    # Extract texts and metadata from chunks
    texts = []
    metadatas = []
    ids = []

    for chunk in chunks:
        # Support both LangChain Document objects and plain dicts
        if hasattr(chunk, 'page_content'):
            text = chunk.page_content
            meta = dict(chunk.metadata) if chunk.metadata else {}
        else:
            text = chunk.get('page_content', chunk.get('content', ''))
            meta = dict(chunk.get('metadata', {}))

        # Store content in metadata for retrieval
        meta['content'] = text[:39000]  # Pinecone metadata limit ~40KB

        # Ensure source_type exists
        if 'source_type' not in meta:
            meta['source_type'] = 'document'

        # Generate unique vector ID — must be ASCII-only for Pinecone
        raw_name = meta.get('doc_name', 'doc')
        safe_name = hashlib.sha256(raw_name.encode()).hexdigest()[:16]
        vec_id = f"{safe_name}_{uuid.uuid4().hex[:12]}"

        # Clean metadata: Pinecone only supports str, int, float, bool, list[str]
        clean_meta = {}
        for k, v in meta.items():
            if v is None:
                clean_meta[k] = ""
            elif isinstance(v, (str, int, float, bool)):
                clean_meta[k] = v
            elif isinstance(v, list):
                clean_meta[k] = [str(item) for item in v]
            else:
                clean_meta[k] = str(v)

        texts.append(text)
        metadatas.append(clean_meta)
        ids.append(vec_id)

    # Embed all texts via Pinecone Inference API in batches of 96
    # (Pinecone Inference recommends ≤96 inputs per call)
    embed_batch_size = 96
    embeddings = []
    for i in range(0, len(texts), embed_batch_size):
        batch_texts = texts[i:i + embed_batch_size]
        embeddings.extend(_embed_texts(embedding_model, batch_texts))

    # Upsert in batches of 100 (Pinecone recommended batch size)
    batch_size = 100
    total_upserted = 0

    for i in range(0, len(ids), batch_size):
        batch_vectors = []
        for j in range(i, min(i + batch_size, len(ids))):
            batch_vectors.append({
                "id": ids[j],
                "values": embeddings[j],
                "metadata": metadatas[j],
            })
        index.upsert(vectors=batch_vectors, namespace=user_id)
        total_upserted += len(batch_vectors)

    print(f"OK  Upserted {total_upserted} vectors to namespace '{user_id}'")
    return total_upserted


# ── Ingest with Advanced RAG ────────────────────────────────────────────────

def ingest_documents(child_chunks: list, parent_records: list,
                     user_id: str, summary_docs: list = None,
                     embedding_model=None):
    """
    Ingest child chunks (and optional summaries) into Pinecone,
    and save parent chunks to SQLite.

    Args:
        child_chunks: List of child Document objects with parent_id in metadata
        parent_records: List of dicts from create_parent_child_chunks()
        user_id: Google user ID for namespace isolation
        summary_docs: Optional list of summary Document objects
        embedding_model: Pinecone client (loads from cache if None)
    """
    # Save parent chunks to SQLite (batch for performance)
    database.save_parent_chunks_batch(parent_records)
    print(f"OK  Saved {len(parent_records)} parent chunks to SQLite")

    # Combine child chunks and summary docs for single upsert
    all_chunks = list(child_chunks) if child_chunks else []
    if summary_docs:
        all_chunks.extend(summary_docs)

    if all_chunks:
        upsert_documents(all_chunks, user_id, embedding_model)


def ingest_note(note_id: int, title: str, content: str,
                user_id: str, embedding_model=None):
    """
    Ingest a research note into Pinecone with source_type='note'.

    Args:
        note_id: SQLite note ID
        title: Note title
        content: Note content text
        user_id: Google user ID for namespace
        embedding_model: Pinecone client (loads from cache if None)
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from langchain_core.documents import Document
    from document_loader import get_adaptive_chunk_params

    total_chars = len(content)
    chunk_size, chunk_overlap = get_adaptive_chunk_params(total_chars)

    doc = Document(
        page_content=content,
        metadata={
            'title': title,
            'source_type': 'note',
            'note_id': note_id,
            'chunk_type': 'child',
            'doc_name': f'note_{note_id}',
            'created_at': date.today().isoformat(),
        }
    )

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", " ", ""]
    )
    chunks = splitter.split_documents([doc])

    upsert_documents(chunks, user_id, embedding_model)
    print(f"OK  Ingested note '{title}' ({len(chunks)} chunks, size={chunk_size})")


# ── BM25 keyword scoring ──────────────────────────────────────────────────────

def _bm25_scores(query: str, corpus: list, k1: float = 1.5, b: float = 0.75) -> list:
    """
    Compute BM25 relevance scores for a query against a corpus of texts.

    This is a pure-Python, dependency-free implementation.  It is intentionally
    simple: tokenisation is whitespace+punctuation split on lowercased text.
    For Thai text this gives reasonable term overlap without a full tokeniser.

    Args:
        query: User query string
        corpus: List of document text strings
        k1: BM25 term saturation parameter (default 1.5)
        b: BM25 length normalisation parameter (default 0.75)

    Returns:
        List of float scores, one per corpus entry, in the same order
    """
    import math
    import re as _re

    def _tokenize(text: str) -> list:
        # Split on whitespace and common punctuation; lowercase
        return _re.split(r'[\s\u3000\uff0c\u3002\uff0e\u0e01-\u0e7f]', text.lower())

    query_terms = [t for t in _tokenize(query) if t]
    if not query_terms:
        return [0.0] * len(corpus)

    tokenized_corpus = [_tokenize(doc) for doc in corpus]
    doc_lengths = [len(doc) for doc in tokenized_corpus]
    avg_dl = sum(doc_lengths) / max(len(doc_lengths), 1)
    N = len(corpus)

    # Document frequency for each query term
    df = {}
    for term in query_terms:
        df[term] = sum(1 for doc in tokenized_corpus if term in doc)

    scores = []
    for doc_idx, doc_tokens in enumerate(tokenized_corpus):
        score = 0.0
        dl = doc_lengths[doc_idx]
        for term in query_terms:
            tf = doc_tokens.count(term)
            if tf == 0:
                continue
            idf = math.log((N - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5) + 1)
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * dl / avg_dl)
            score += idf * (numerator / denominator)
        scores.append(score)

    return scores


# ── Retrieval ────────────────────────────────────────────────────────────────

def retrieve_unified(query: str, user_id: str, k: int = 3,
                     source_type: Optional[str] = None,
                     doc_name: Optional[str] = None,
                     expand_parents: bool = True,
                     embedding_model=None,
                     hybrid: bool = True,
                     timeout: float = _PINECONE_QUERY_TIMEOUT,
                     return_scores: bool = False) -> list:
    """
    Unified retrieval from Pinecone with:
    - Metadata filtering (source_type and/or doc_name)
    - Re-rank cutoff: fetch _PRE_FILTER_MULTIPLIER * k candidates, keep k
    - Hybrid search: fuse BM25 keyword scores with Pinecone cosine scores
    - Timeout + fallback: returns [] gracefully if Pinecone is slow/unavailable
    - Parent-child expansion: child matches are swapped for richer parent context
    - Near-duplicate removal: fingerprint-based deduplication before returning
    - Context length control: truncate combined content to _MAX_CONTEXT_CHARS

    Args:
        query: Natural language query
        user_id: Google user ID (Pinecone namespace)
        k: Final number of results to return
        source_type: Optional filter — "document", "note", "web_page", or None
        doc_name: Optional filter — restrict to a specific document by doc_name
        expand_parents: If True, replace child chunks with their parent content
        embedding_model: Pinecone client (loads from cache if None)
        hybrid: If True, fuse BM25 scores with vector scores
        timeout: Pinecone query timeout in seconds (falls back to [] on timeout)
        return_scores: If True, return list of (Document, score) tuples instead of
            plain Document objects. Default False for backward compatibility.

    Returns:
        list: Retrieved Document objects (up to k), deduplicated and context-capped.
            If return_scores=True, returns list of (Document, score) tuples instead.
    """
    from langchain_core.documents import Document

    if not query or not user_id:
        return []

    if embedding_model is None:
        embedding_model = get_embedding_model()

    try:
        index = get_pinecone_index()
    except Exception as e:
        print(f"Warning: Could not connect to Pinecone: {e}")
        return []

    # Embed the query (cache-backed)
    query_embedding = _embed_query(embedding_model, query)

    # ── Metadata filter construction ──────────────────────────────────────────
    # Support stacking multiple filters with $and for precise scoping
    filter_conditions = []
    if source_type:
        filter_conditions.append({"source_type": {"$eq": source_type}})
    if doc_name:
        filter_conditions.append({"doc_name": {"$eq": doc_name}})

    if len(filter_conditions) == 1:
        filter_dict = filter_conditions[0]
    elif len(filter_conditions) > 1:
        filter_dict = {"$and": filter_conditions}
    else:
        filter_dict = None

    # ── Re-rank cutoff: fetch more candidates than needed ─────────────────────
    pre_filter_k = min(int(k * _PRE_FILTER_MULTIPLIER), _MAX_PRE_FILTER)

    # ── Query Pinecone with timeout ───────────────────────────────────────────
    # Wrap the Pinecone SDK call in a thread so we can enforce a wall-clock
    # timeout without blocking the Streamlit event loop.  Falls back to an
    # empty result list on timeout or any connection error.
    try:
        results = None
        with ThreadPoolExecutor(max_workers=1) as _ex:
            future = _ex.submit(
                index.query,
                vector=query_embedding,
                top_k=pre_filter_k,
                namespace=user_id,
                filter=filter_dict,
                include_metadata=True,
            )
            try:
                results = future.result(timeout=timeout)
            except Exception:
                # TimeoutError or any exception from the Pinecone call
                results = None

    except Exception as e:
        print(f"Warning: Pinecone query error: {e}")
        return []

    if not results or not results.get("matches"):
        return []

    matches = results["matches"]

    # ── Convert Pinecone results to (Document, score) pairs ───────────────────
    docs_with_scores: list = []
    for match in matches:
        meta = dict(match.get("metadata", {}))
        content = meta.pop("content", "")
        vector_score = float(match.get("score", 0.0))
        # Skip results below the minimum score threshold
        if vector_score < _MIN_SCORE_THRESHOLD:
            continue
        docs_with_scores.append((
            Document(page_content=content, metadata=meta),
            vector_score,
        ))

    if not docs_with_scores:
        return []

    # ── Hybrid search: fuse BM25 keyword scores with vector scores ────────────
    if hybrid and len(docs_with_scores) > 1:
        corpus = [d.page_content for d, _ in docs_with_scores]
        bm25_raw = _bm25_scores(query, corpus)
        # Normalise BM25 scores to [0, 1] range
        max_bm25 = max(bm25_raw) if max(bm25_raw) > 0 else 1.0
        bm25_norm = [s / max_bm25 for s in bm25_raw]

        fused = []
        for (doc, vec_score), bm25_s in zip(docs_with_scores, bm25_norm):
            fused_score = (
                _HYBRID_VECTOR_WEIGHT * vec_score
                + _HYBRID_BM25_WEIGHT * bm25_s
            )
            fused.append((doc, fused_score))

        # Re-sort by fused score descending
        fused.sort(key=lambda x: x[1], reverse=True)
        docs_with_scores = fused

    # Keep only final k after re-ranking
    if return_scores:
        return [(doc, score) for doc, score in docs_with_scores[:k]]

    docs = [doc for doc, _ in docs_with_scores[:k]]

    # ── Parent-Child expansion ────────────────────────────────────────────────
    if expand_parents and docs:
        parent_ids = list({
            doc.metadata.get('parent_id')
            for doc in docs
            if doc.metadata.get('parent_id')
        })
        if parent_ids:
            parents = database.get_parent_chunks_batch(parent_ids)
            expanded = []
            seen_parents: set = set()
            for doc in docs:
                pid = doc.metadata.get('parent_id')
                if pid and pid in parents and pid not in seen_parents:
                    seen_parents.add(pid)
                    parent_data = parents[pid]
                    expanded.append(Document(
                        page_content=parent_data['content'],
                        metadata={
                            **doc.metadata,
                            'chunk_type': 'parent_expanded',
                            'parent_page': parent_data.get('page_number'),
                            'parent_section': parent_data.get('section'),
                        }
                    ))
                elif not pid:
                    expanded.append(doc)
            docs = expanded if expanded else docs

    # ── Near-duplicate removal ────────────────────────────────────────────────
    # Use a more robust fingerprint: first 300 chars normalised to lowercase
    # This catches slightly different formatting of the same underlying passage
    seen_sigs: set = set()
    unique: list = []
    for doc in docs:
        # Normalise: lowercase, collapse whitespace
        sig = " ".join(doc.page_content[:300].lower().split())
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            unique.append(doc)

    # ── Context length control ────────────────────────────────────────────────
    # Truncate each doc so total concatenated context stays within token budget.
    # Estimation: 1 token ≈ 4 characters (English/Thai average).
    budget = _MAX_CONTEXT_CHARS
    context_capped: list = []
    for doc in unique:
        if budget <= 0:
            break
        if len(doc.page_content) > budget:
            # Truncate this doc to remaining budget
            doc = Document(
                page_content=doc.page_content[:budget],
                metadata=doc.metadata,
            )
        context_capped.append(doc)
        budget -= len(doc.page_content)

    return context_capped


# ── Embedding cache utilities ─────────────────────────────────────────────────

def get_embedding_cache_stats() -> dict:
    """Return current embedding cache statistics."""
    return {
        "entries": len(_EMBEDDING_CACHE),
        "max_entries": _EMBEDDING_CACHE_MAX,
        "usage_pct": round(100 * len(_EMBEDDING_CACHE) / _EMBEDDING_CACHE_MAX, 1),
    }


def clear_embedding_cache() -> None:
    """Flush the in-process embedding cache."""
    global _EMBEDDING_CACHE
    _EMBEDDING_CACHE.clear()
    print("OK  Embedding cache cleared")


# ── Delete ───────────────────────────────────────────────────────────────────

def delete_document(doc_name: str, user_id: str) -> None:
    """
    Delete all vectors for a document from Pinecone by listing and filtering.

    Since Pinecone serverless doesn't support delete-by-metadata-filter directly,
    we list vectors in the namespace and delete by ID prefix matching.

    Args:
        doc_name: The doc_name metadata value to match
        user_id: Google user ID (Pinecone namespace)
    """
    try:
        index = get_pinecone_index()
        safe_prefix = doc_name.replace(" ", "_")[:50]

        # Try listing vectors with the prefix
        try:
            listed = index.list(namespace=user_id, prefix=safe_prefix)
            if listed and hasattr(listed, 'vectors'):
                vec_ids = [v for v in listed.vectors]
                if vec_ids:
                    index.delete(ids=vec_ids, namespace=user_id)
                    print(f"OK  Deleted {len(vec_ids)} vectors for '{doc_name}'")
                    return
        except Exception:
            pass

        # Fallback: query to find matching IDs then delete
        try:
            pc = get_embedding_model()
            dummy_vec = _embed_query(pc, doc_name)
            results = index.query(
                vector=dummy_vec,
                top_k=1000,
                namespace=user_id,
                filter={"doc_name": {"$eq": doc_name}},
                include_metadata=False,
            )
            if results and results.get("matches"):
                ids_to_delete = [m["id"] for m in results["matches"]]
                if ids_to_delete:
                    for i in range(0, len(ids_to_delete), 1000):
                        batch = ids_to_delete[i:i + 1000]
                        index.delete(ids=batch, namespace=user_id)
                    print(f"OK  Deleted {len(ids_to_delete)} vectors for '{doc_name}'")
        except Exception as e:
            print(f"Warning: Could not delete vectors for '{doc_name}': {e}")

    except Exception as e:
        print(f"Warning: Delete operation failed: {e}")


def delete_by_metadata(filter_key: str, filter_value, user_id: str) -> None:
    """
    Delete vectors from Pinecone matching a metadata filter.

    Args:
        filter_key: Metadata key to filter on (e.g., 'note_id', 'web_page_id')
        filter_value: Value to match
        user_id: Google user ID (Pinecone namespace)
    """
    try:
        index = get_pinecone_index()
        pc = get_embedding_model()
        dummy_vec = _embed_query(pc, str(filter_value))
        results = index.query(
            vector=dummy_vec,
            top_k=1000,
            namespace=user_id,
            filter={filter_key: {"$eq": filter_value}},
            include_metadata=False,
        )
        if results and results.get("matches"):
            ids_to_delete = [m["id"] for m in results["matches"]]
            if ids_to_delete:
                for i in range(0, len(ids_to_delete), 1000):
                    batch = ids_to_delete[i:i + 1000]
                    index.delete(ids=batch, namespace=user_id)
                print(f"OK  Deleted {len(ids_to_delete)} vectors matching {filter_key}={filter_value}")
    except Exception as e:
        print(f"Warning: delete_by_metadata failed: {e}")


# ── Enhanced Retrieval Pipeline ───────────────────────────────────────────────

def enhanced_retrieve(query: str, user_id: str, k: int = 5,
                      source_type: Optional[str] = None,
                      doc_name: Optional[str] = None,
                      expand_parents: bool = True,
                      embedding_model=None,
                      hybrid: bool = True,
                      use_query_router: bool = True,
                      use_reranker: bool = True,
                      rerank_top_n: Optional[int] = None,
                      use_llm_fallback: bool = False,
                      score_threshold: Optional[float] = None,
                      metadata_filters: Optional[dict] = None,
                      max_context_chars: Optional[int] = None) -> list:
    """
    Enhanced retrieval pipeline that wraps retrieve_unified() with:
    1. Query classification (via query_router) for dynamic topK
    2. Metadata filter enhancement (category, domain, doc_id)
    3. Dynamic topK based on classification confidence
    4. Score threshold filtering (post-retrieval)
    5. Re-ranking with cutoff (via reranker)
    6. Enhanced deduplication (content fingerprinting)
    7. Context length control
    8. Fallback strategy (retry without filters on empty results)

    This function does NOT modify retrieve_unified() — it calls it and
    adds post-processing layers. Existing code paths are unaffected.

    Args:
        query: Natural language query (Thai or English)
        user_id: Google user ID (Pinecone namespace)
        k: Final number of results to return (default 5)
        source_type: Optional filter — "document", "note", "web_page"
        doc_name: Optional filter — restrict to a specific document
        expand_parents: If True, replace child chunks with parent content
        embedding_model: Pinecone client (loads from cache if None)
        hybrid: If True, fuse BM25 scores with vector scores
        use_query_router: If True, classify query for dynamic topK
        use_reranker: If True, re-rank results after retrieval
        rerank_top_n: Override reranker output count (defaults to k)
        use_llm_fallback: If True, use LLM for ambiguous query classification
        score_threshold: Minimum score to keep (None = use default 0.30)
        metadata_filters: Extra Pinecone metadata filters dict,
                          e.g. {"category": "ML", "domain": "cs"}
        max_context_chars: Override max context chars (None = use default 8000)

    Returns:
        list: Retrieved Document objects, deduplicated and context-capped
    """
    if not query or not user_id:
        return []

    # ── Step 1: Query classification for dynamic topK ────────────────────────
    classification = None
    retrieval_k = k

    if use_query_router:
        try:
            from query_router import classify_query
            classification = classify_query(query, use_llm_fallback=use_llm_fallback)
            # Use suggested topK from classification, but at least k
            retrieval_k = max(classification.suggested_top_k, k)
            print(f"OK  Query classified: {classification.category} "
                  f"(conf={classification.confidence:.2f}, topK={retrieval_k}, "
                  f"src={classification.source})")
        except Exception as e:
            print(f"Warning: Query router failed, using default topK={k}: {e}")
            retrieval_k = k

    # ── Step 2: Retrieve with potentially higher topK ────────────────────────
    # We ask for more candidates than needed so re-ranking has material to work with
    fetch_k = retrieval_k if use_reranker else k

    results = retrieve_unified(
        query=query,
        user_id=user_id,
        k=fetch_k,
        source_type=source_type,
        doc_name=doc_name,
        expand_parents=expand_parents,
        embedding_model=embedding_model,
        hybrid=hybrid,
    )

    # ── Step 3: Fallback strategy — retry without filters on empty results ───
    if not results and (source_type or doc_name):
        print("Info: No results with filters, retrying without filters...")
        results = retrieve_unified(
            query=query,
            user_id=user_id,
            k=fetch_k,
            source_type=None,
            doc_name=None,
            expand_parents=expand_parents,
            embedding_model=embedding_model,
            hybrid=hybrid,
        )

    if not results:
        return []

    # ── Step 4: Re-ranking with cutoff ───────────────────────────────────────
    if use_reranker and len(results) > 1:
        try:
            from reranker import rerank
            final_n = rerank_top_n if rerank_top_n is not None else k
            results = rerank(
                query=query,
                documents=results,
                top_n=final_n,
                use_cross_encoder=False,  # Use lightweight fallback by default
            )
            print(f"OK  Reranked {len(results)} results (top_n={final_n})")
        except Exception as e:
            print(f"Warning: Reranker failed, using original order: {e}")
            results = results[:k]
    else:
        results = results[:k]

    # ── Step 5: Enhanced deduplication ────────────────────────────────────────
    # retrieve_unified() already does basic dedup, but we add a second pass
    # with more aggressive fingerprinting (first 200 chars, collapsed whitespace)
    seen_sigs: set = set()
    unique: list = []
    for doc in results:
        sig = " ".join(doc.page_content[:200].lower().split())
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            unique.append(doc)
    results = unique

    # ── Step 6: Context length control ───────────────────────────────────────
    # Apply a final context budget (retrieve_unified already does this, but
    # re-ranking may have changed the order, so we re-apply)
    from langchain_core.documents import Document as _Doc
    budget = max_context_chars if max_context_chars is not None else _MAX_CONTEXT_CHARS
    context_capped: list = []
    for doc in results:
        if budget <= 0:
            break
        if len(doc.page_content) > budget:
            doc = _Doc(
                page_content=doc.page_content[:budget],
                metadata=doc.metadata,
            )
        context_capped.append(doc)
        budget -= len(doc.page_content)

    return context_capped


# ── Legacy compatibility aliases ─────────────────────────────────────────────

def initialize_embeddings():
    """Legacy alias for get_embedding_model(). Returns the Pinecone client."""
    return get_embedding_model()


def print_retrieval_results(query: str, results: list):
    """Pretty-print retrieval results to stdout."""
    print("\n" + "=" * 80)
    print("RETRIEVAL RESULTS")
    print("=" * 80)
    print(f"Query: '{query}'")
    print(f"Retrieved: {len(results)} document(s)\n")

    for i, doc in enumerate(results, 1):
        print(f"\n--- Document {i} ---")
        print(f"Content:\n{doc.page_content[:500]}")
        if len(doc.page_content) > 500:
            print("...")
        if doc.metadata:
            print("Metadata:")
            for key, value in doc.metadata.items():
                if key != 'content':
                    print(f"  {key}: {value}")

    print("\n" + "=" * 80 + "\n")
