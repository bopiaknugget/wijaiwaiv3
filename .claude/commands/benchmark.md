Run the end-to-end RAG benchmark suite against the live API and Pinecone.

```bash
# Full benchmark suite (Tests A–I)
python benchmark.py

# Capture a baseline before changes
python benchmark_prompts.py --save benchmark_results_before.json

# Capture results after changes
python benchmark_prompts.py --save benchmark_results_after.json

# Compare before vs after
python benchmark_prompts.py --compare benchmark_results_before.json benchmark_results_after.json
```

**Benchmark Tests (benchmark.py)**:
- **A** — Small talk routing: `is_small_talk()` returns True, retrieval skipped
- **B** — Simple factual query: embed + retrieve + LLM latency measured
- **C** — Thai language query: bilingual support validation
- **D** — Long query edge case: >200 chars, must NOT route as small talk
- **E** — Empty/whitespace: graceful handling, no crash
- **F** — Consistency: same query 3×, all succeed
- **G** — Streaming: tokens arrive via `generate_answer_stream()`
- **H** — Real data retrieval: ingest test doc, verify parent-child expansion, dedup, BM25 scoring
- **I** — Live namespace probe: query real user namespace, report retrieval quality metrics

**Requirements**:
- Valid `.env` with `OPENTHAI_API_KEY` and Pinecone credentials
- Live Pinecone index (`wijaiwai`) must be reachable
- Live OpenThaiGPT API must be reachable
- Test H uses `benchmark_test` namespace — verify cleanup if re-running

**Note**: This is a live end-to-end test. It consumes API tokens and makes real Pinecone writes. Do NOT run in CI automatically.
