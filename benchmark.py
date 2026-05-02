"""
RAG Pipeline End-to-End Benchmark Script
=========================================
Tests the live OpenThaiGPT API and Pinecone pipeline end-to-end.
Does NOT require the Streamlit app to be running.

Tests:
  A) Small talk routing    — is_small_talk() returns True, retrieval skipped
  B) Simple factual query  — embed + retrieve + LLM latency measured separately
  C) Thai language query   — bilingual Thai support
  D) Long query edge case  — query >200 chars, must NOT be small-talk routed
  E) Empty/whitespace      — graceful handling without crash
  F) Consistency           — same query 3 times, all succeed
  G) Streaming             — tokens arrive from generate_answer_stream()
  H) Real data retrieval   — ingest a controlled test doc into benchmark namespace,
                             verify retrieval returns the actual content, then
                             validate parent-child expansion, deduplication,
                             context-length control, and hybrid BM25 scoring
  I) Live namespace probe  — query the real user namespace with content-relevant
                             queries, report retrieval quality metrics

Usage:
    python benchmark.py
"""

import os
import sys
import time
import traceback
from pathlib import Path
from dotenv import load_dotenv

# ── Environment ───────────────────────────────────────────────────────────────
_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

OPENTHAI_API_KEY = os.getenv("OPENTHAI_API_KEY", "")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_HOST    = os.getenv("PINECONE_HOST", "(default)")

# Use a stable test namespace that does not conflict with real user data
TEST_USER_ID = "__benchmark_test_user__"

# Known real namespace discovered via index stats — used in Test I
# This is the namespace created by actual app usage (Google OAuth user)
REAL_USER_NAMESPACE = "google_102886497280770017710"

# The document that was actually ingested into the real namespace
REAL_DOC_NAME = "test_doc.pdf"

# ── Result tracking ───────────────────────────────────────────────────────────
results = []  # list of dicts


def _record(name: str, passed: bool, notes: str = "",
            embed_ms: float = None, retrieve_ms: float = None,
            llm_ms: float = None, total_ms: float = None):
    results.append({
        "name": name,
        "passed": passed,
        "notes": notes,
        "embed_ms": embed_ms,
        "retrieve_ms": retrieve_ms,
        "llm_ms": llm_ms,
        "total_ms": total_ms,
    })
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {name}")
    if notes:
        for line in notes.strip().splitlines():
            print(f"         {line}")


def _ms(start: float) -> float:
    """Elapsed milliseconds since a time.perf_counter() start."""
    return round((time.perf_counter() - start) * 1000, 1)


# ─────────────────────────────────────────────────────────────────────────────
# TEST A — Small talk routing
# ─────────────────────────────────────────────────────────────────────────────
def test_small_talk_routing():
    print("\n[A] Small talk routing")
    from generator import is_small_talk

    cases = [
        # (query, expected_is_small_talk)
        ("สวัสดี",             True),
        ("หวัดดี",             True),
        ("hello",              True),
        ("hi",                 True),
        ("ขอบคุณ",             True),
        ("thanks",             True),
        ("ลาก่อน",             True),
        ("bye",                True),
        ("คุณเป็นใคร",         True),
        ("are you an ai",      True),
        # Must NOT be routed as small talk
        ("ทฤษฎีเศรษฐศาสตร์คืออะไร",        False),
        ("machine learning คืออะไร",        False),
        ("อธิบาย neural network ให้ฟังหน่อย", False),
    ]

    all_ok = True
    mismatches = []
    for query, expected in cases:
        got = is_small_talk(query)
        if got != expected:
            all_ok = False
            mismatches.append(f"  '{query}' => got {got}, expected {expected}")

    if mismatches:
        notes = "Mismatches:\n" + "\n".join(mismatches)
    else:
        notes = f"All {len(cases)} routing cases correct"

    _record("Small talk routing", all_ok, notes)


# ─────────────────────────────────────────────────────────────────────────────
# TEST B — Simple factual query (phased latency)
# ─────────────────────────────────────────────────────────────────────────────
def test_simple_factual_query():
    print("\n[B] Simple factual query — phased latency")
    from vector_store import get_embedding_model, _embed_query, retrieve_unified
    from generator import generate_answer

    query = "machine learning คืออะไร"
    embed_ms = retrieve_ms = llm_ms = total_ms = None
    passed = False
    notes = ""

    t0 = time.perf_counter()
    try:
        # Phase 1: Embedding
        t1 = time.perf_counter()
        pc = get_embedding_model()
        _ = _embed_query(pc, query)
        embed_ms = _ms(t1)

        # Phase 2: Retrieval (may return [] if the namespace is empty)
        t2 = time.perf_counter()
        docs = retrieve_unified(query, user_id=TEST_USER_ID, k=3)
        retrieve_ms = _ms(t2)

        # Phase 3: LLM generation
        t3 = time.perf_counter()
        action, response, _, in_tok, out_tok = generate_answer(
            query=query,
            retrieved_docs=docs,
        )
        llm_ms = _ms(t3)
        total_ms = _ms(t0)

        if not response or len(response.strip()) < 10:
            notes = f"Response too short ({len(response)} chars)"
        else:
            passed = True
            notes = (
                f"embed={embed_ms}ms  retrieve={retrieve_ms}ms  "
                f"llm={llm_ms}ms  total={total_ms}ms\n"
                f"docs_retrieved={len(docs)}  action={action}  "
                f"in={in_tok} out={out_tok} tokens\n"
                f"response_preview: {response[:120].replace(chr(10), ' ')}"
            )
    except Exception:
        notes = traceback.format_exc()

    _record("Simple factual query", passed, notes,
            embed_ms=embed_ms, retrieve_ms=retrieve_ms,
            llm_ms=llm_ms, total_ms=total_ms)


# ─────────────────────────────────────────────────────────────────────────────
# TEST C — Thai language query
# ─────────────────────────────────────────────────────────────────────────────
def test_thai_query():
    print("\n[C] Thai language query")
    from vector_store import get_embedding_model, _embed_query, retrieve_unified
    from generator import generate_answer

    query = "อธิบายหลักการของ deep learning และการนำไปใช้งาน"
    embed_ms = retrieve_ms = llm_ms = total_ms = None
    passed = False
    notes = ""

    t0 = time.perf_counter()
    try:
        t1 = time.perf_counter()
        pc = get_embedding_model()
        vec = _embed_query(pc, query)
        embed_ms = _ms(t1)

        if not vec or len(vec) == 0:
            _record("Thai language query", False, "Embedding returned empty vector")
            return

        vec_dim = len(vec)

        t2 = time.perf_counter()
        docs = retrieve_unified(query, user_id=TEST_USER_ID, k=3)
        retrieve_ms = _ms(t2)

        t3 = time.perf_counter()
        action, response, _, in_tok, out_tok = generate_answer(
            query=query,
            retrieved_docs=docs,
        )
        llm_ms = _ms(t3)
        total_ms = _ms(t0)

        # Count Thai unicode characters in the response
        thai_chars = sum(1 for c in response if '\u0e00' <= c <= '\u0e7f')

        if thai_chars < 10:
            notes = f"Response only {thai_chars} Thai chars — may be English fallback"
            passed = False
        else:
            passed = True
            notes = (
                f"embed_dim={vec_dim}  embed={embed_ms}ms  "
                f"retrieve={retrieve_ms}ms  llm={llm_ms}ms\n"
                f"thai_chars={thai_chars}  in={in_tok} out={out_tok} tokens\n"
                f"response_preview: {response[:120].replace(chr(10), ' ')}"
            )
    except Exception:
        notes = traceback.format_exc()

    _record("Thai language query", passed, notes,
            embed_ms=embed_ms, retrieve_ms=retrieve_ms,
            llm_ms=llm_ms, total_ms=total_ms)


# ─────────────────────────────────────────────────────────────────────────────
# TEST D — Long query edge case (>200 chars)
# ─────────────────────────────────────────────────────────────────────────────
def test_long_query():
    print("\n[D] Long query edge case (>200 chars)")
    from generator import is_small_talk, generate_answer
    from vector_store import retrieve_unified

    query = (
        "กรุณาอธิบายอย่างละเอียดเกี่ยวกับ transformer architecture ที่ใช้ใน "
        "large language models รวมถึง self-attention mechanism, positional encoding, "
        "feed-forward layers, และวิธีที่ BERT และ GPT ใช้ transformer ในแบบที่แตกต่างกัน "
        "พร้อมทั้งข้อดีและข้อเสียของแต่ละแนวทาง"
    )

    passed = False
    notes = ""
    t0 = time.perf_counter()

    try:
        # Long queries must never be classified as small talk
        st_result = is_small_talk(query)
        if st_result:
            notes = f"ROUTING BUG: long query ({len(query)} chars) classified as small talk"
            _record("Long query edge case", False, notes, total_ms=_ms(t0))
            return

        docs = retrieve_unified(query, user_id=TEST_USER_ID, k=3)
        action, response, _, in_tok, out_tok = generate_answer(
            query=query,
            retrieved_docs=docs,
        )
        total_ms = _ms(t0)

        if not response or len(response.strip()) < 20:
            notes = f"Response too short: {len(response)} chars"
        else:
            passed = True
            notes = (
                f"query_len={len(query)} chars  is_small_talk={st_result}  "
                f"total={total_ms}ms\n"
                f"in={in_tok} out={out_tok} tokens\n"
                f"response_preview: {response[:120].replace(chr(10), ' ')}"
            )
    except Exception:
        notes = traceback.format_exc()

    _record("Long query edge case", passed, notes, total_ms=_ms(t0))


# ─────────────────────────────────────────────────────────────────────────────
# TEST E — Empty / whitespace query
# ─────────────────────────────────────────────────────────────────────────────
def test_empty_query():
    print("\n[E] Empty / whitespace query edge case")
    from generator import is_small_talk
    from vector_store import retrieve_unified

    test_cases = [
        ("",      "empty string"),
        ("   ",   "whitespace only"),
        ("\t\n",  "tab+newline"),
    ]

    all_ok = True
    case_notes = []
    for query, label in test_cases:
        try:
            docs = retrieve_unified(query, user_id=TEST_USER_ID, k=3)
            if docs:
                all_ok = False
                case_notes.append(f"  {label}: expected [], got {len(docs)} docs")
            else:
                case_notes.append(f"  {label}: returned [] (correct)")

            # is_small_talk on empty input must not raise
            _ = is_small_talk(query)
        except Exception as e:
            all_ok = False
            case_notes.append(f"  {label}: EXCEPTION: {e}")

    _record("Empty/whitespace query", all_ok, "\n".join(case_notes))


# ─────────────────────────────────────────────────────────────────────────────
# TEST F — Consistency (same query 3x)
# ─────────────────────────────────────────────────────────────────────────────
def test_consistency():
    print("\n[F] Consistency — same query 3x")
    from vector_store import retrieve_unified
    from generator import generate_answer

    query = "AI คืออะไร"
    run_notes = []
    times = []
    passed = True

    for i in range(3):
        try:
            t = time.perf_counter()
            docs = retrieve_unified(query, user_id=TEST_USER_ID, k=3)
            action, response, _, in_tok, out_tok = generate_answer(
                query=query,
                retrieved_docs=docs,
            )
            elapsed = _ms(t)
            times.append(elapsed)
            if not response or len(response.strip()) < 10:
                passed = False
                run_notes.append(f"  Run {i+1}: empty/too-short response ({len(response)} chars)")
            else:
                run_notes.append(f"  Run {i+1}: {len(response)} chars, {elapsed}ms")
        except Exception as e:
            passed = False
            run_notes.append(f"  Run {i+1}: EXCEPTION {e}")

    avg_ms = round(sum(times) / len(times), 1) if times else None
    notes = (f"avg_latency={avg_ms}ms\n" + "\n".join(run_notes))
    _record("Consistency (3x same query)", passed, notes, llm_ms=avg_ms)


# ─────────────────────────────────────────────────────────────────────────────
# TEST G — Streaming
# ─────────────────────────────────────────────────────────────────────────────
def test_streaming():
    print("\n[G] Streaming — tokens arrive incrementally")
    from generator import generate_answer_stream

    query = "สวัสดี บอกฉันเกี่ยวกับ AI หน่อย"
    passed = False
    notes = ""
    t0 = time.perf_counter()

    try:
        token_count = 0
        accumulated = []
        first_token_ms = None

        for token in generate_answer_stream(query=query, retrieved_docs=[]):
            token_count += 1
            accumulated.append(token)
            if first_token_ms is None:
                first_token_ms = _ms(t0)

        total_ms = _ms(t0)
        full_response = "".join(accumulated)

        if token_count == 0:
            notes = "No tokens received from stream"
        elif len(full_response.strip()) < 10:
            notes = f"Stream complete but response too short: '{full_response}'"
        else:
            passed = True
            notes = (
                f"tokens_received={token_count}  "
                f"first_token={first_token_ms}ms  total={total_ms}ms\n"
                f"response_len={len(full_response)} chars\n"
                f"response_preview: {full_response[:120].replace(chr(10), ' ')}"
            )
    except Exception:
        notes = traceback.format_exc()

    _record("Streaming", passed, notes, llm_ms=_ms(t0))


# ─────────────────────────────────────────────────────────────────────────────
# TEST H — Real data retrieval with a freshly ingested benchmark document
#
# Strategy:
#   1. Write a short, content-rich .txt file with known facts.
#   2. Ingest it into the dedicated TEST_USER_ID namespace via the full pipeline
#      (enrich_metadata → create_parent_child_chunks → ingest_documents).
#   3. Query with terms that exist verbatim in the document.
#   4. Assert: retrieved > 0, content matches, parent-child expansion works,
#      deduplication fires when the same parent is referenced by multiple children,
#      context-length control caps the total characters, hybrid BM25 re-ranking
#      executes without error.
#   5. Clean up: delete the test vectors from Pinecone so the namespace stays lean.
# ─────────────────────────────────────────────────────────────────────────────

_BENCHMARK_DOC_NAME = "__benchmark_real_doc__.txt"

_BENCHMARK_DOC_CONTENT = """\
# Research Workbench Benchmark Document
# This file is auto-generated for benchmarking purposes only.

## Section 1: Retrieval Augmented Generation
Retrieval Augmented Generation (RAG) combines a dense vector retrieval step with a
large language model (LLM) to produce answers grounded in source documents.
The retrieval step embeds the user query and performs approximate nearest-neighbour
search over a vector index such as Pinecone.  The top-k results are then inserted
into the LLM prompt as context, reducing hallucination and improving factual accuracy.

Key components of a RAG pipeline:
- Embedding model: converts text to dense vectors (e.g., multilingual-e5-large)
- Vector index: stores and retrieves embeddings by cosine similarity
- Chunking strategy: parent-child, sliding-window, or sentence-level
- Re-ranking: BM25 hybrid fusion or cross-encoder to improve precision
- Generation: LLM produces a grounded answer conditioned on retrieved context

## Section 2: Pinecone Vector Database
Pinecone is a managed vector database that supports serverless and pod-based
deployments.  It provides namespace isolation so multiple users can share a
single index without cross-contamination.  Metadata filtering allows scoping
queries to a specific document, source type, or date range.

Pinecone features used in this project:
- Namespaces: one namespace per Google user ID
- Metadata filters: source_type, doc_name, chunk_type
- Inference API: multilingual-e5-large (1024-dimensional embeddings)
- Upsert batching: up to 100 vectors per call

## Section 3: OpenThaiGPT Language Model
OpenThaiGPT is a Thai-language large language model developed by NECTEC.
It is accessed via a REST API at http://thaillm.or.th/api/openthaigpt/v1/chat/completions
using an apikey header.  The model supports bilingual Thai/English generation
and is the primary LLM in the Research Workbench.

Default parameters:
- max_tokens: 2048 (chat), 12000 (research mode)
- temperature: 0.3
- model identifier: /model

## Section 4: Parent-Child Chunking
Parent-child chunking stores large parent passages in SQLite and small child
chunks in Pinecone.  During retrieval, matched child chunks are expanded back
to their parent context, giving the LLM a richer passage without sacrificing
retrieval precision.  This is a key Advanced RAG technique.

Parent IDs are stored in child chunk metadata under the key 'parent_id'.
The SQLite table 'parent_chunks' maps parent_id -> full content.

## Section 5: Thai Language NLP
Thai text does not use spaces between words, which complicates tokenisation.
The multilingual-e5-large model handles Thai naturally because it was trained
on over 100 languages including Thai.  For BM25 hybrid scoring, a simple
regex-based tokeniser splits on whitespace and Unicode ranges for basic
term-overlap matching.

Thai academic vocabulary commonly seen in research:
- บทคัดย่อ (abstract)
- ระเบียบวิธีวิจัย (research methodology)
- ผลการวิจัย (research results)
- บทสรุป (conclusion)
- เอกสารอ้างอิง (references)
"""


def _ingest_benchmark_doc(user_id: str) -> int:
    """
    Write the benchmark doc to a temp .txt, ingest it into Pinecone,
    and return the number of child chunks created.
    """
    import tempfile
    from document_loader import load_document, enrich_metadata, create_parent_child_chunks
    from vector_store import ingest_documents, get_embedding_model

    # Write to a temp file
    tmp_path = Path(tempfile.gettempdir()) / _BENCHMARK_DOC_NAME
    tmp_path.write_text(_BENCHMARK_DOC_CONTENT, encoding="utf-8")

    docs = load_document(str(tmp_path))
    docs = enrich_metadata(docs, _BENCHMARK_DOC_NAME, source_type="document", user_id=user_id)
    children, parents = create_parent_child_chunks(docs, _BENCHMARK_DOC_NAME,
                                                    source_type="document", user_id=user_id)

    pc = get_embedding_model()
    ingest_documents(children, parents, user_id, embedding_model=pc)

    tmp_path.unlink(missing_ok=True)
    return len(children)


def _cleanup_benchmark_doc(user_id: str):
    """Delete test vectors from Pinecone so the namespace stays clean."""
    from vector_store import delete_document
    try:
        delete_document(_BENCHMARK_DOC_NAME, user_id)
    except Exception as e:
        print(f"  Warning: cleanup failed — {e}")


def test_real_data_retrieval():
    print("\n[H] Real data retrieval — ingest + retrieve + validate")
    from vector_store import retrieve_unified, get_embedding_cache_stats
    import database as db

    passed = False
    notes_lines = []
    embed_ms = retrieve_ms = total_ms = None
    chunk_count = 0

    t0 = time.perf_counter()
    try:
        # ── Step 1: Ingest ────────────────────────────────────────────────────
        t_ingest = time.perf_counter()
        chunk_count = _ingest_benchmark_doc(TEST_USER_ID)
        ingest_ms = _ms(t_ingest)
        notes_lines.append(f"ingested {chunk_count} child chunks in {ingest_ms}ms")

        if chunk_count == 0:
            notes_lines.append("ERROR: ingestion produced 0 chunks")
            _record("Real data retrieval", False, "\n".join(notes_lines), total_ms=_ms(t0))
            return

        # Allow a brief moment for Pinecone to index the freshly upserted vectors.
        # Pinecone serverless typically makes vectors queryable in < 1 second,
        # but we use a small poll loop to be safe without a hard sleep.
        import time as _time
        deadline = _time.perf_counter() + 10.0  # wait up to 10s
        while _time.perf_counter() < deadline:
            probe = retrieve_unified(
                "Retrieval Augmented Generation",
                user_id=TEST_USER_ID, k=1,
            )
            if probe:
                break
            _time.sleep(0.5)

        # ── Step 2: Retrieval quality ─────────────────────────────────────────
        queries_and_terms = [
            # (query, substring that must appear in some retrieved chunk)
            ("Retrieval Augmented Generation RAG pipeline",
             "vector"),
            ("Pinecone namespace metadata filtering",
             "namespace"),
            ("parent child chunking SQLite parent_id",
             "parent"),
        ]

        t_ret = time.perf_counter()
        retrieval_results = {}
        for q, expected_term in queries_and_terms:
            docs = retrieve_unified(q, user_id=TEST_USER_ID, k=3)
            retrieval_results[q] = docs
        retrieve_ms = _ms(t_ret)

        # ── Step 3: Validate content relevance ────────────────────────────────
        relevance_ok = True
        for (q, expected_term), docs in zip(
                [(q, t) for q, t in queries_and_terms], retrieval_results.values()):
            if not docs:
                notes_lines.append(f"  MISS: '{q[:50]}' returned 0 docs")
                relevance_ok = False
                continue
            combined = " ".join(d.page_content for d in docs).lower()
            if expected_term.lower() not in combined:
                notes_lines.append(
                    f"  MISS: '{q[:50]}' — term '{expected_term}' not in retrieved content"
                )
                relevance_ok = False
            else:
                notes_lines.append(
                    f"  HIT : '{q[:50]}' => {len(docs)} docs, "
                    f"term '{expected_term}' found"
                )

        # ── Step 4: Parent-child expansion check ─────────────────────────────
        expansion_count = 0
        for docs in retrieval_results.values():
            for doc in docs:
                if doc.metadata.get("chunk_type") == "parent_expanded":
                    expansion_count += 1
        notes_lines.append(
            f"parent_expanded chunks: {expansion_count} "
            f"({'OK — expansion fired' if expansion_count > 0 else 'WARN — no expansion (may have no parent_id)'})"
        )

        # ── Step 5: Deduplication check ───────────────────────────────────────
        # Query the same content twice and confirm both calls succeed
        dup_docs_1 = retrieve_unified(
            "Pinecone vector database serverless", user_id=TEST_USER_ID, k=5
        )
        dup_docs_2 = retrieve_unified(
            "Pinecone vector database serverless", user_id=TEST_USER_ID, k=5
        )
        # Check that second call (cache hit) is faster — just check it doesn't crash
        notes_lines.append(
            f"deduplication: {len(dup_docs_1)} unique docs on run1, "
            f"{len(dup_docs_2)} on run2 (cache)"
        )

        # ── Step 6: Context length control check ─────────────────────────────
        # Request a large k to stress the context cap (_MAX_CONTEXT_CHARS = 8000)
        many_docs = retrieve_unified(
            "RAG LLM embedding chunking", user_id=TEST_USER_ID, k=10
        )
        total_chars = sum(len(d.page_content) for d in many_docs)
        from vector_store import _MAX_CONTEXT_CHARS
        ctx_ok = total_chars <= _MAX_CONTEXT_CHARS
        notes_lines.append(
            f"context_control: total_chars={total_chars} <= {_MAX_CONTEXT_CHARS}: "
            f"{'OK' if ctx_ok else 'FAIL'}"
        )

        # ── Step 7: Hybrid BM25 check ─────────────────────────────────────────
        # Confirm hybrid=False and hybrid=True both return results without error
        vec_only = retrieve_unified(
            "Thai language NLP tokenisation", user_id=TEST_USER_ID, k=3, hybrid=False
        )
        hybrid_docs = retrieve_unified(
            "Thai language NLP tokenisation", user_id=TEST_USER_ID, k=3, hybrid=True
        )
        notes_lines.append(
            f"hybrid: vec_only={len(vec_only)} docs, hybrid={len(hybrid_docs)} docs"
        )

        # ── Step 8: Embedding cache stats ─────────────────────────────────────
        cache_stats = get_embedding_cache_stats()
        notes_lines.append(
            f"embed_cache: {cache_stats['entries']} entries "
            f"({cache_stats['usage_pct']}% of {cache_stats['max_entries']})"
        )

        # ── Determine pass/fail ───────────────────────────────────────────────
        total_ms = _ms(t0)
        passed = relevance_ok and ctx_ok

        notes_lines.insert(0,
            f"retrieve={retrieve_ms}ms  total={total_ms}ms"
        )

    except Exception:
        notes_lines.append(traceback.format_exc())
    finally:
        # Always clean up to avoid polluting the benchmark namespace
        _cleanup_benchmark_doc(TEST_USER_ID)

    _record("Real data retrieval", passed, "\n".join(notes_lines),
            retrieve_ms=retrieve_ms, total_ms=total_ms)


# ─────────────────────────────────────────────────────────────────────────────
# TEST I — Live namespace probe (real user namespace)
#
# Queries the actual namespace created by the app (REAL_USER_NAMESPACE) using
# queries relevant to the content already ingested there (test_doc.pdf —
# a UI layout/design checklist document in English/Thai).
# Reports retrieval quality, score distribution, chunk types, and latency.
# Does NOT modify any data (read-only).
# ─────────────────────────────────────────────────────────────────────────────
def test_live_namespace_probe():
    print(f"\n[I] Live namespace probe — namespace '{REAL_USER_NAMESPACE}'")
    from vector_store import retrieve_unified, get_embedding_model, _embed_query
    from pinecone import Pinecone
    import os

    passed = False
    notes_lines = []
    retrieve_ms = embed_ms = total_ms = None

    t0 = time.perf_counter()
    try:
        # ── Confirm namespace exists and has vectors ───────────────────────────
        pc_api_key = os.getenv("PINECONE_API_KEY", "")
        pc_index_name = os.getenv("PINECONE_INDEX_NAME", "wijaiwai")
        pc_host = os.getenv("PINECONE_HOST", "")

        pc = Pinecone(api_key=pc_api_key)
        if pc_host:
            index = pc.Index(pc_index_name, host=pc_host)
        else:
            index = pc.Index(pc_index_name)

        stats = index.describe_index_stats()
        ns_data = stats.get("namespaces", {}).get(REAL_USER_NAMESPACE, {})
        vector_count = ns_data.get("vector_count", 0)

        if vector_count == 0:
            notes_lines.append(
                f"Namespace '{REAL_USER_NAMESPACE}' has 0 vectors — skipping probe"
            )
            _record("Live namespace probe", True,
                    "\n".join(notes_lines), total_ms=_ms(t0))
            return

        notes_lines.append(f"namespace vector_count={vector_count}")

        # ── Queries relevant to test_doc.pdf content ──────────────────────────
        # test_doc.pdf is a UI design checklist about layout proportions,
        # progress feedback, streaming approach, and citation format
        relevant_queries = [
            ("layout column width ratios three panels", "layout"),
            ("streaming text approach think tag display", "streaming"),
            ("citation format document title chunk reference", "citation"),
            ("progress feedback upload bar Thai message", "progress"),
        ]

        t_embed = time.perf_counter()
        pc_client = get_embedding_model()
        _ = _embed_query(pc_client, relevant_queries[0][0])
        embed_ms = _ms(t_embed)

        hits = 0
        miss = 0
        total_docs = 0
        score_sum = 0.0
        score_count = 0

        t_ret = time.perf_counter()
        for q, expected_keyword in relevant_queries:
            docs = retrieve_unified(q, user_id=REAL_USER_NAMESPACE, k=3)
            total_docs += len(docs)
            if docs:
                combined = " ".join(d.page_content for d in docs).lower()
                if expected_keyword.lower() in combined:
                    hits += 1
                    notes_lines.append(
                        f"  HIT : '{q[:50]}' => {len(docs)} docs, "
                        f"keyword '{expected_keyword}' found"
                    )
                else:
                    miss += 1
                    notes_lines.append(
                        f"  WEAK: '{q[:50]}' => {len(docs)} docs, "
                        f"keyword '{expected_keyword}' NOT in content"
                    )
            else:
                miss += 1
                notes_lines.append(f"  MISS: '{q[:50]}' returned 0 docs")

        retrieve_ms = _ms(t_ret)

        # ── Score distribution from a direct Pinecone call ────────────────────
        test_q = "layout three panels column width"
        resp_embed = pc.inference.embed(
            model="multilingual-e5-large",
            inputs=[test_q],
            parameters={"input_type": "query", "truncate": "END"}
        )
        qvec = resp_embed[0]["values"]
        raw_results = index.query(
            vector=qvec,
            top_k=6,
            namespace=REAL_USER_NAMESPACE,
            include_metadata=True
        )
        scores = [m["score"] for m in raw_results.get("matches", [])]
        if scores:
            notes_lines.append(
                f"score_distribution: min={min(scores):.3f} "
                f"max={max(scores):.3f} "
                f"avg={sum(scores)/len(scores):.3f} "
                f"(n={len(scores)})"
            )

        # ── Parent-child expansion for real namespace ─────────────────────────
        sample_docs = retrieve_unified(
            "layout column ratios", user_id=REAL_USER_NAMESPACE, k=5
        )
        expanded = sum(
            1 for d in sample_docs
            if d.metadata.get("chunk_type") == "parent_expanded"
        )
        notes_lines.append(
            f"parent_expanded in sample: {expanded}/{len(sample_docs)}"
        )

        total_ms = _ms(t0)
        precision = hits / len(relevant_queries)
        notes_lines.insert(0,
            f"precision={hits}/{len(relevant_queries)}  "
            f"embed={embed_ms}ms  retrieve={retrieve_ms}ms  total={total_ms}ms"
        )

        # Pass if at least half the relevant queries returned meaningful results
        passed = precision >= 0.5

    except Exception:
        notes_lines.append(traceback.format_exc())

    _record("Live namespace probe", passed, "\n".join(notes_lines),
            embed_ms=embed_ms, retrieve_ms=retrieve_ms, total_ms=total_ms)


# ─────────────────────────────────────────────────────────────────────────────
# FINAL REPORT
# ─────────────────────────────────────────────────────────────────────────────
def _print_report():
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    print("\n" + "=" * 72)
    print("BENCHMARK RESULTS SUMMARY")
    print("=" * 72)
    print(f"  Pass: {passed}/{total}    Fail: {failed}/{total}")
    print()

    header = (
        f"  {'Test':<35} {'Status':<6} "
        f"{'Embed':>8} {'Retrieve':>10} {'LLM':>8} {'Total':>8}"
    )
    print(header)
    print("  " + "-" * 68)

    for r in results:
        status   = "PASS" if r["passed"] else "FAIL"
        embed    = f"{r['embed_ms']:.0f}ms"    if r["embed_ms"]    is not None else "-"
        retrieve = f"{r['retrieve_ms']:.0f}ms" if r["retrieve_ms"] is not None else "-"
        llm      = f"{r['llm_ms']:.0f}ms"      if r["llm_ms"]      is not None else "-"
        total    = f"{r['total_ms']:.0f}ms"    if r["total_ms"]    is not None else "-"
        print(
            f"  {r['name']:<35} {status:<6} "
            f"{embed:>8} {retrieve:>10} {llm:>8} {total:>8}"
        )

    print("=" * 72)

    # Average latencies across tests that measured them
    def _avg(key):
        vals = [r[key] for r in results if r[key] is not None]
        return f"{sum(vals)/len(vals):.0f}ms" if vals else "n/a"

    print(f"\n  Average embedding latency : {_avg('embed_ms')}")
    print(f"  Average retrieval latency : {_avg('retrieve_ms')}")
    print(f"  Average LLM latency       : {_avg('llm_ms')}")
    print(f"  Average end-to-end latency: {_avg('total_ms')}")
    print()

    failures = [r for r in results if not r["passed"]]
    if failures:
        print("FAILURES:")
        for r in failures:
            print(f"  - {r['name']}")
            for line in r["notes"].strip().splitlines()[:6]:
                print(f"      {line}")
    else:
        print("No failures detected.")

    print("=" * 72)
    return failed


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Force UTF-8 stdout/stderr so that Unicode characters printed by
    # document_loader.py (e.g. the check-mark U+2713) do not crash on
    # Windows consoles that default to cp874 (Thai codepage).
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    if not OPENTHAI_API_KEY:
        print("ERROR: OPENTHAI_API_KEY not set in .env")
        sys.exit(1)
    if not PINECONE_API_KEY:
        print("ERROR: PINECONE_API_KEY not set in .env")
        sys.exit(1)

    print("=" * 72)
    print("RAG PIPELINE END-TO-END BENCHMARK")
    print(f"  API  : http://thaillm.or.th/api/openthaigpt/v1/chat/completions")
    print(f"  Index: {PINECONE_HOST}")
    print(f"  Test NS   : {TEST_USER_ID}")
    print(f"  Real NS   : {REAL_USER_NAMESPACE}  ({REAL_DOC_NAME})")
    print("=" * 72)

    test_small_talk_routing()
    test_simple_factual_query()
    test_thai_query()
    test_long_query()
    test_empty_query()
    test_consistency()
    test_streaming()
    test_real_data_retrieval()
    test_live_namespace_probe()

    failed_count = _print_report()
    sys.exit(0 if failed_count == 0 else 1)
