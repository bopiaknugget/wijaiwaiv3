# Skill: wijaiwaiv-review

Project-specific code review for the WijaiWai Research Workbench. Run on any PR, new module, or significant change before merging.

## Review Checklist

### 1. Frozen Code Integrity
- [ ] `rag_pipeline.py` is not modified
- [ ] No new code imports from `rag_pipeline.py`

### 2. User Data Isolation
- [ ] Every SQLite query on user-scoped tables (`research_notes`, `documents`, `web_pages`, `editor_documents`, `token_usage`) passes `user_id` as a parameter
- [ ] Every Pinecone query and upsert includes `namespace=user_id`
- [ ] No query can return data across user boundaries
- [ ] `users` table is read-only after OAuth callback

### 3. Database Safety
- [ ] All SQL uses parameterized queries (`?`) — zero f-string SQL
- [ ] All DB access uses `get_db_connection()` context manager
- [ ] New tables use `CREATE TABLE IF NOT EXISTS`
- [ ] New columns use `_add_column_if_missing()` for migrations
- [ ] No `DROP TABLE`, `TRUNCATE`, or unscoped `DELETE` statements

### 4. Streamlit Patterns
- [ ] All `st.widget()` calls have unique `key=` arguments
- [ ] Inputs needing programmatic reset use Value Proxy pattern
- [ ] `st.session_state` reads use `.get()` with sensible defaults
- [ ] No blocking operations without a spinner

### 5. Thai/English Bilingual Support
- [ ] LLM prompts handle both Thai and English input
- [ ] No ASCII-only string assumptions (length checks, truncation)
- [ ] `multilingual-e5-large` embedding handles the content correctly

### 6. Pinecone Operations
- [ ] `source_type` metadata set on every upsert: `"document"`, `"note"`, or `"web"`
- [ ] SHA-256 embedding cache not bypassed
- [ ] Hybrid search weights (BM25 0.3, vector 0.7) unchanged without documented rationale
- [ ] Parent-child: child chunks reference correct `parent_id` in SQLite

### 7. LLM Integration
- [ ] `record_token_usage()` called after every LLM API call
- [ ] `<think>` tag handling: `parse_think_content()` used in UI, stripped in `generator.py` history
- [ ] Chat history capped at last 6 messages
- [ ] Token budgets: 2048 (chat), 3000 (normal), 12000 (research mode)
- [ ] Temperature 0.3 unless documented rationale

### 8. Error Handling
- [ ] LLM API failures caught and shown gracefully (no raw tracebacks to user)
- [ ] Pinecone failures caught — retrieval failure must not crash the app
- [ ] Redis/anti-abuse failures are fail-open (app continues)
- [ ] File ingestion errors show user-friendly messages

### 9. Security
- [ ] No API keys in code — all via `os.getenv()`
- [ ] No f-string SQL anywhere
- [ ] Google OAuth `user_id` is the isolation key — never trust user-provided user IDs
- [ ] Anti-abuse middleware not bypassed for authenticated endpoints

### 10. Testing
- [ ] New functions have unit tests in `tests/`
- [ ] Tests use `fakeredis` for Redis, mock or `:memory:` SQLite for DB
- [ ] CI passes without real Redis (integration tests skip gracefully)
- [ ] `benchmark.py` baseline run if RAG pipeline was changed

## Review Output Format

For each section, report:
- **PASS** — no issues
- **WARN** — minor issue, acceptable with note
- **FAIL** — must fix before merge

**Summary**: [X PASS / Y WARN / Z FAIL]

If any FAIL: describe the exact issue and the required fix.
