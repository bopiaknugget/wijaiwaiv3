# CLAUDE.md — WijaiWai Research Workbench v2.1

## Rules

1. **Check context-hub before using any API**: `chub search [api-name]` → `chub get [api-name] --lang py`
2. **Never modify `rag_pipeline.py`** — frozen legacy Phase 1 reference.
3. **Never query Pinecone without a namespace** — exposes all users' data.
4. **Always pass `user_id` to all DB and Pinecone operations** — per-user isolation is mandatory.
5. **Call `record_token_usage()` after every LLM API call** — required for per-user token stats.

## Project

AI-powered academic research platform. Streamlit 3-panel UI (Sidebar / Research Workbench / Assistant). Bilingual Thai/English. Requires Google OAuth login.

**Entry points**: `app.py` (web UI) · `main.py` (CLI: `--ingest` / `--query`)

## Tech Stack

| Component | Detail |
|---|---|
| LLM | OpenThaiGPT — `POST http://thaillm.or.th/api/openthaigpt/v1/chat/completions`, header: `apikey: {OPENTHAI_API_KEY}`, model: `/model` |
| Vector DB | Pinecone index `wijaiwai`, namespace = Google user ID, `multilingual-e5-large` 1024-dim via Pinecone Inference API |
| Database | SQLite `./Database/research_notes.db` — 7 tables |
| Auth | Google OAuth 2.0 via `auth.py` |
| Rate limiting | Redis (optional) via `anti_abuse/` — fails open if Redis unavailable |

## Environment (`.env`)

```
OPENTHAI_API_KEY=
PINECONE_API_KEY=
PINECONE_INDEX_NAME=wijaiwai
PINECONE_HOST=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8501/oauth2callback
```

No quotes or spaces. Loaded via `python-dotenv`.

## LLM Defaults

- **Temperature**: 0.3 · **max_tokens**: 2048 (chat) · 3000 (normal) · 12000 (research mode)
- **Token cost**: $0.4/1M, displayed in THB (1 USD = 35 THB)
- **Chat history**: capped at last 6 messages in `st.session_state`
- **`<think>` tags**: `parse_think_content()` in `app.py` splits them; `generator.py` strips before re-phrasing

## Database Schema (SQLite)

| Table | Key Columns |
|---|---|
| `users` | `id TEXT PK` (Google user ID), `email`, `name`, `picture`, `created_at`, `last_login` |
| `research_notes` | `id`, `user_id`, `title`, `content`, `timestamp` |
| `documents` | `id`, `user_id`, `filename`, `file_type`, `chunk_count`, `db_path`, `timestamp` |
| `parent_chunks` | `id TEXT PK`, `content`, `source_file`, `page_number`, `section`, `timestamp` |
| `web_pages` | `id`, `user_id`, `url`, `title`, `summary`, `chunk_count`, `timestamp` |
| `editor_documents` | `id`, `user_id`, `name` (unique per user), `title`, `content`, `timestamp` |
| `token_usage` | `id`, `user_id`, `function_name`, `input_tokens`, `output_tokens`, `timestamp` |

Always use `get_db_connection()` context manager. Tables auto-created by `initialize_database()`.
Migrations: use `_add_column_if_missing(cursor, table, column, col_type)`.

## Pinecone

- Single index: `wijaiwai` · Namespace = Google user ID (never omit)
- Hybrid search: BM25 (0.3) + vector (0.7)
- Parent-child: child chunks in Pinecone → parent content in SQLite `parent_chunks`
- `source_type` metadata: `"document"` · `"note"` · `"web"`
- Embedding cache: SHA-256 hash guards against redundant Inference API calls — do not bypass

## Streamlit Patterns

- **Value Proxy pattern** for inputs needing programmatic reset: separate `*_val` key + `st.rerun()`
- Widget keys must be unique across all of `app.py`
- Editor documents stored in SQLite `editor_documents` — NOT the filesystem
- `./user_data/` is legacy import only

## Module Map

| File | Role |
|---|---|
| `app.py` | Streamlit UI, OAuth splash, session state, 3-panel layout, `<think>` tag parsing |
| `auth.py` | Google OAuth 2.0 — auth URL, callback, user info |
| `generator.py` | OpenThaiGPT calls, streaming, intent detection (`is_small_talk`, `is_edit_intent`), editor functions |
| `vector_store.py` | Pinecone ops, hybrid retrieval, parent-child expansion, SHA-256 embedding cache |
| `document_loader.py` | PDF/TXT/DOCX loading, parent-child chunking, metadata enrichment |
| `database.py` | All SQLite CRUD, `initialize_database()`, `record_token_usage()` |
| `reviewer.py` | Thesis advisor review, chunked processing, color-coded feedback |
| `web_scraper.py` | URL extraction (trafilatura + BS4), AI summarization, Pinecone ingestion |
| `query_router.py` | Query classification/routing |
| `anti_abuse/` | Rate limiting, token quotas, concurrency limits (Redis, fail-open) |
| `rag_pipeline.py` | **FROZEN — do not touch** |

## Agent Routing Guide

| Task | Agent |
|---|---|
| RAG pipeline, Pinecone, OpenThaiGPT, embeddings | `rag-llm-dev` |
| Streamlit UI/UX, CSS, layout, widget patterns | `streamlit-ui-expert` |
| Rate limiting, Redis, anti-abuse | `api-rate-limit-architect` |
| SQLite schema, CRUD, migrations, DB queries | `sqlite-db-agent` |
| pytest, CI/CD, test writing, Locust, coverage | `test-quality-agent` |
| Redis performance benchmarking | `redis-benchmarker` |
| Cross-cutting Pinecone+SQLite integrations | `ai-web-dev` |

## Commands

| Command | Purpose |
|---|---|
| `/run-app` | Start Streamlit UI |
| `/run-tests` | Run pytest suite |
| `/run-lint` | Run flake8 linter |
| `/ingest-doc` | Ingest a document via CLI |
| `/check-health` | Verify env vars + core imports |
| `/benchmark` | Run end-to-end benchmark suite |

## Skills

| Skill | Purpose |
|---|---|
| `debug-rag` | Systematic RAG debugging (8-step checklist) |
| `add-feature` | Feature addition workflow (5-phase) |
| `wijaiwaiv-review` | Project-specific code review (10-dimension checklist) |

## Common Pitfalls

- Do not modify `rag_pipeline.py` — legacy reference only
- Streamlit widget keys must be unique; use Value Proxy for programmatic reset
- Never query Pinecone without namespace — exposes all user data
- Editor documents go to SQLite `editor_documents`, not filesystem
- SHA-256 embedding cache must not be bypassed — causes redundant Inference API charges
- Call `record_token_usage()` after every LLM call — breaks per-user stats if skipped
- `parent_chunks.id` is TEXT (UUID), not integer
- `users.id` is TEXT (Google user ID), not integer
