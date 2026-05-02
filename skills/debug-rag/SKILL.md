# Skill: debug-rag

Systematic RAG pipeline debugging for the Research Workbench. Use when retrieval returns no results, wrong results, irrelevant context, or LLM answers are hallucinated/missing.

## Step 1: Verify Pinecone Connectivity

```python
from vector_store import get_pinecone_index
index = get_pinecone_index()
stats = index.describe_index_stats()
print(stats)
```

Check: index name is `wijaiwai`, expected namespaces present, vector count > 0.

## Step 2: Verify User Namespace Has Vectors

```python
user_ns = stats.namespaces.get("USER_ID_HERE")
print(f"Vector count: {user_ns.vector_count if user_ns else 'NAMESPACE NOT FOUND'}")
```

If missing or count = 0: ingestion failed or wrong `user_id` was used.

## Step 3: Test Raw Retrieval

```python
from vector_store import retrieve_unified
results = retrieve_unified("your test query", user_id="USER_ID", k=5)
for r in results:
    print(r['source_type'], r['score'], r['content'][:100])
```

Check: results returned, scores > 0.5, `source_type` matches expected content type.

## Step 4: Check Parent-Child Expansion

```python
from database import get_db_connection
with get_db_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM parent_chunks")
    print("Parent chunks:", cursor.fetchone()[0])
    cursor.execute("SELECT id, source_file FROM parent_chunks LIMIT 5")
    for row in cursor.fetchall():
        print(row)
```

If `parent_chunks` empty: document ingested without parent-child chunking, or chunking failed.

## Step 5: Check Embedding Cache

SHA-256 of content is the cache key. If content changed after ingestion, new vectors won't exist. Force re-ingest with modified document or clear the cache entry.

## Step 6: Test Hybrid Search Weights

Default: BM25 0.3 + vector 0.7. If keyword-heavy Thai queries fail, BM25 weight may be too low. Check `vector_store.py` hybrid search parameters.

## Step 7: Test LLM Generation Separately

```python
from generator import generate_answer
action, answer, editor, inp, out = generate_answer(
    "your query",
    retrieved_docs=[{"content": "manually injected test context", "source_type": "document"}],
    chat_history=[]
)
print(answer)
```

If generation fails: API key issue, network problem, or token limit exceeded.

## Step 8: Check Intent Routing

```python
from generator import is_small_talk, is_edit_intent
query = "your query"
print("Small talk:", is_small_talk(query))
print("Edit intent:", is_edit_intent(query))
```

If `is_small_talk()` returns True for a real query: regex is over-matching. Check `generator.py` patterns.

## Diagnostic Checklist

After running steps above, report:
- [ ] Pinecone index reachable
- [ ] User namespace exists with N vectors
- [ ] Retrieval returns results for test query
- [ ] Parent chunks present in SQLite
- [ ] Correct `source_type` on vectors
- [ ] Intent routing correct for query
- [ ] LLM generation works with manual context
- [ ] **Root cause identified**: [FILL IN]
