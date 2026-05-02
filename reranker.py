"""
Reranker Module — Lightweight Re-ranking for RAG Pipeline

Provides re-ranking of retrieved documents to improve precision:
- Cross-encoder re-ranking using sentence-transformers (if available)
- Fallback to simple keyword-overlap scoring (zero dependencies)
- Configurable cutoff to reduce final result count

Design decisions:
- Cross-encoder is loaded lazily and cached (heavy on first load, fast after)
- Fallback scoring is always available with zero extra dependencies
- All functions accept and return LangChain Document objects
"""

import re
from typing import Optional

# ── Cross-Encoder (lazy-loaded) ──────────────────────────────────────────────

_cross_encoder = None
_cross_encoder_loaded = False


def _get_cross_encoder():
    """
    Lazily load a lightweight cross-encoder model for re-ranking.

    Uses 'cross-encoder/ms-marco-MiniLM-L-6-v2' — small (~80MB) and fast.
    Returns None if sentence-transformers is not installed.
    """
    global _cross_encoder, _cross_encoder_loaded

    if _cross_encoder_loaded:
        return _cross_encoder

    _cross_encoder_loaded = True
    try:
        from sentence_transformers import CrossEncoder
        _cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
        print("OK  Cross-encoder loaded: ms-marco-MiniLM-L-6-v2")
    except ImportError:
        print("Info: sentence-transformers not available, using fallback reranker")
        _cross_encoder = None
    except Exception as e:
        print(f"Warning: Could not load cross-encoder: {e}")
        _cross_encoder = None

    return _cross_encoder


# ── Fallback: keyword overlap scoring ────────────────────────────────────────

def _tokenize(text: str) -> set:
    """Simple whitespace + punctuation tokenizer, lowercased."""
    return set(re.split(r'[\s\u3000.,;:!?()\[\]{}"\']+', text.lower())) - {""}


def _keyword_score(query: str, document: str) -> float:
    """
    Compute a simple keyword overlap score between query and document.

    Returns a value in [0.0, 1.0] representing the fraction of query
    tokens found in the document.

    Args:
        query: The search query
        document: The document text

    Returns:
        float: Overlap score between 0.0 and 1.0
    """
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0
    doc_tokens = _tokenize(document)
    overlap = query_tokens & doc_tokens
    return len(overlap) / len(query_tokens)


# ── Main rerank function ─────────────────────────────────────────────────────

def rerank(query: str, documents: list, top_n: Optional[int] = None,
           use_cross_encoder: bool = True) -> list:
    """
    Re-rank a list of documents by relevance to the query.

    Strategy:
    1. If cross-encoder is available and enabled: use it for precise scoring
    2. Otherwise: fall back to keyword overlap scoring
    3. Sort by score descending and return top_n results

    Args:
        query: The search query (Thai or English)
        documents: List of LangChain Document objects to re-rank
        top_n: Number of top results to return. If None, returns all (re-sorted).
        use_cross_encoder: If True, attempt to use cross-encoder model.
                           Set False to force lightweight fallback.

    Returns:
        list: Re-ranked Document objects (up to top_n)
    """
    if not documents or not query:
        return documents

    if top_n is not None and top_n <= 0:
        return []

    # Attempt cross-encoder scoring
    encoder = _get_cross_encoder() if use_cross_encoder else None

    if encoder is not None:
        try:
            pairs = [(query, doc.page_content) for doc in documents]
            scores = encoder.predict(pairs)
            scored_docs = list(zip(documents, scores))
        except Exception as e:
            print(f"Warning: Cross-encoder scoring failed, using fallback: {e}")
            scored_docs = [
                (doc, _keyword_score(query, doc.page_content))
                for doc in documents
            ]
    else:
        # Fallback: keyword overlap scoring
        scored_docs = [
            (doc, _keyword_score(query, doc.page_content))
            for doc in documents
        ]

    # Sort by score descending
    scored_docs.sort(key=lambda x: x[1], reverse=True)

    # Apply top_n cutoff
    if top_n is not None:
        scored_docs = scored_docs[:top_n]

    return [doc for doc, _ in scored_docs]
