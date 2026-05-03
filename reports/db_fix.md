# SQLite Database Usage Review and Fix Plan

Date: 2026-05-03

Scope: review current database usage and plan changes. Product code was not edited.

## Executive Summary

This app currently uses SQLite as the runtime application database. I found no active Python runtime path that uses PostgreSQL, `psycopg`, SQLAlchemy, Alembic, or `DATABASE_URL`.

The main database layer is `database.py`, with SQLite stored at:

```text
Path(__file__).parent / "Database" / "research_notes.db"
```

The correct direction is not a database migration. The fix should keep SQLite, clean up stale PostgreSQL planning references, tighten user/project scoping in newer Reference Vault helpers, and finish moving active product paths away from legacy `documents`, `research_notes`, `web_pages`, and `parent_chunks` tables.

## Product Constraints Applied

- Keep SQLite. Do not migrate to PostgreSQL.
- SQLite is the source of truth.
- Pinecone is a vector index only.
- Do not delete legacy data.
- Notes and Web Scraping must not remain active product scope.
- Preserve `database.py` function names where possible.
- Reference Vault contains uploaded papers and OpenAlex-imported metadata.
- OpenAlex API keys and base URLs must stay in `.env`, not in code.
- Schema changes should be idempotent through `initialize_database()`.
- No `DROP TABLE`, no destructive data reset.

## Current Database Architecture

### Runtime SQLite Boundary

`database.py` owns SQLite access:

- `database.py:19` defines `DB_PATH`.
- `database.py:47` defines `get_db_connection()`.
- `database.py:59` is the only runtime `sqlite3.connect(...)` found in app code.
- `database.py:66` defines `initialize_database()`.
- `database.py:1305-1306` initializes database tables on import.

This matches the intended SQLite-first architecture.

### Tables Currently Created

Legacy tables:

- `research_notes`
- `documents`
- `parent_chunks`
- `web_pages`
- `token_usage`
- `users`
- `editor_documents`

v3 tables already present in current `database.py`:

- `projects`
- `reference_vault_documents`
- `reference_vault_chunks`
- `openalex_search_cache`
- `citation_candidates`
- `citation_logs`

Important observation: the current schema already has a partial v3 Reference Vault foundation. Future work should extend it instead of replacing it.

## PostgreSQL Check

### Active runtime code

No active runtime PostgreSQL implementation was found.

Not found in runtime Python:

- `psycopg`
- `psycopg2`
- `asyncpg`
- SQLAlchemy database engine
- Alembic migrations
- `DATABASE_URL`
- `db_postgres.py`
- `database_postgres.py`

### Stale or conflicting documentation

PostgreSQL appears only in old planning/docs:

- `0_change_architecture_report.md` still recommends replacing SQLite with PostgreSQL.
- `instructions.md` still references `_context_packs/data_model_postgresql.md`.

These are not runtime bugs, but they are planning hazards. They should be marked obsolete or superseded by SQLite v3 decisions in a documentation-only cleanup.

## Active Database Usage Inventory

### `database.py`

Role: SQLite source of truth and CRUD layer.

Good:

- Uses one central SQLite path.
- Uses `get_db_connection()` for CRUD functions.
- Uses `CREATE TABLE IF NOT EXISTS`.
- Preserves legacy tables.
- Adds v3 Reference Vault/OpenAlex/citation tables.
- Uses parameterized SQL for values.

Needs later fix:

- `_add_column_if_missing()` uses f-string SQL for table/column identifiers. Current callers use hardcoded internal identifiers, so this is not an immediate injection path. Still, the helper should validate identifiers before formatting SQL.
- Some user-scoped reads/updates allow missing `user_id`, which is risky for v3 paths.
- Some legacy functions still support unscoped reads/deletes for backward compatibility.

Specific caution points:

- `database.py:406` `get_reference_vault_document(...)` allows lookup by `document_id` without `user_id`.
- `database.py:449` `update_reference_vault_document_status(...)` updates by `document_id` only.
- `database.py:474` `update_reference_vault_document_metadata(...)` updates by `document_id` only.
- `database.py:503` `delete_reference_vault_document(...)` has optional `user_id`; should be required for active user paths.
- `database.py:628` `get_reference_vault_chunk(...)` reads by `chunk_id` only.
- `database.py:639` `get_reference_vault_chunks_by_document(...)` reads by `document_id` only.
- `database.py:699` `update_reference_vault_chunk_vector_id(...)` updates by `chunk_id` only.
- `database.py:716` `delete_reference_vault_chunks_by_document(...)` has optional `user_id`.
- `database.py:857` `load_all_notes(...)` supports unscoped legacy note loading.
- `database.py:877` `delete_note_by_id(...)` supports unscoped legacy note delete.
- `database.py:1041` `load_all_web_pages(...)` supports unscoped legacy web loading.
- `database.py:1065` `delete_web_page_by_id(...)` supports unscoped legacy web delete.
- `database.py:1078`, `database.py:1090`, and `database.py:1109` update/read web pages by ID without `user_id`.

### `app.py`

Role: Streamlit UI and product workflow caller.

Current DB calls:

- Workbench editor persistence uses `save_editor_document()` and `list_editor_documents()` at `app.py:81-103`.
- Initial session state still loads legacy `documents` through `load_all_documents(user_id)` at `app.py:376-380`.
- Left Reference Vault panel uses `list_reference_vault_documents(user_id)` at `app.py:877`.
- Legacy document fallback deletion deletes Pinecone vectors, parent chunks, and legacy document metadata at `app.py:891-913`.
- Workbench and chat token tracking use `record_token_usage(...)` across generation/review/chat flows.
- Document-source Workbench flows list active, Docling-succeeded Reference Vault records at `app.py:1070-1074` and `app.py:1330-1333`.

Good:

- Most active user-facing calls pass `user_id`.
- The UI already reads Reference Vault tables.
- OpenAlex search UI is disabled until backend hooks are complete.
- Legacy citation generation is disabled from v3 UI.

Needs later fix:

- `processed_docs` still initializes from legacy `documents`.
- The Reference Vault left panel falls back to legacy document store when no v3 Vault docs exist.
- Legacy document deletion still touches `parent_chunks` and `documents`.
- Active UI should use Reference Vault documents/chunks only for uploaded papers.

### `docling_ingestion.py`

Role: PDF ingestion into Reference Vault.

Current DB calls:

- `create_default_project(user_id)`
- `save_reference_vault_document(...)` with `processing`
- `save_reference_vault_document(...)` with `active` / `succeeded`
- `save_reference_vault_document(...)` with `failed`

Good:

- Uses `database.py`, not raw SQLite.
- Writes uploaded PDF status to Reference Vault.
- Preserves failure state in SQLite.

Needs later fix:

- Ensure `paper_name`, `author_display`, DOI/OpenAlex metadata, and source origin are consistently set when available.
- Consider adding a dedicated status update helper instead of repeated full `INSERT OR REPLACE` calls if partial metadata loss becomes a risk.

### `llamaindex_pinecone_rag.py`

Role: Reference Vault indexing/retrieval orchestration.

Current DB calls:

- `update_reference_vault_document_metadata(...)`
- `get_reference_vault_document(...)`
- `get_reference_vault_chunks_by_document(...)`
- `delete_reference_vault_chunks_by_document(...)`
- `save_reference_vault_chunks_batch(...)`
- `update_reference_vault_chunk_vector_id(...)`
- `get_reference_vault_chunks_by_vector_ids(...)`
- `get_reference_vault_chunks_by_ids(...)`

Good:

- Retrieval verifies Pinecone candidates against SQLite.
- Pinecone is treated as vector index, not source of truth.
- Retrieval filters by `user_id` and optional `project_id`.

Needs later fix:

- `_delete_existing_vectors()` calls `get_reference_vault_chunks_by_document(document_id)` and filters user in Python. Prefer a scoped DB query.
- `update_reference_vault_chunk_vector_id(vector_id, vector_id)` relies on chunk ID matching vector ID. This should be explicit and verified.
- Metadata updates should include user/project scope to prevent cross-user updates if IDs collide or are exposed.

### `vector_store.py`

Role: legacy Pinecone RAG path.

Current DB calls:

- `save_parent_chunks_batch(parent_records)`
- `get_parent_chunks_batch(parent_ids)`

Good:

- Uses SQLite for parent chunk expansion, Pinecone for vectors.

Needs later fix:

- This is legacy parent-child storage, not v3 Reference Vault chunk storage.
- Active Reference Vault ingestion should not write to `parent_chunks`.
- Keep this code for compatibility only until all active UI paths use Docling + LlamaIndex + Reference Vault.

### `auth.py`

Role: OAuth callback.

Current DB call:

- `save_user(user_info)` at `auth.py:144`.

Good:

- Uses SQLite through `database.py`.
- `users.id` is text Google user ID, matching local agent constraints.

Needs later fix:

- None for PostgreSQL. Keep SQLite.

### `citation_generator.py`

Role: legacy citation generation path.

Current DB call:

- `record_token_usage(...)` at `citation_generator.py:423`.

Needs later fix:

- Legacy citation generation remains APA-like behavior. Final v3 citation rendering must come from verified Reference Vault metadata in SQLite and produce `[author_name, paper_name]`.

### Tests

Current tests use temp SQLite databases by monkeypatching `database.DB_PATH`.

Raw `sqlite3.connect(...)` appears in tests only:

- `tests/test_database_reference_vault_schema.py`
- `tests/test_docling_ingestion.py`

This is acceptable for schema assertions, but app/runtime code should keep using `get_db_connection()`.

## Non-SQLite Database-Like Stores

### Pinecone

Pinecone remains active as vector index:

- `vector_store.py`
- `llamaindex_pinecone_rag.py`
- `main.py`
- benchmark scripts

This is expected. Pinecone must not become source of truth.

### Chroma

`rag_pipeline.py` still contains a Chroma path using `./Database/chroma_db`.

This appears legacy/demo code, not the active Streamlit v3 Reference Vault path. It should be clearly marked legacy or removed from active docs later to avoid confusion with SQLite source of truth.

## Main Risks

1. Stale PostgreSQL docs can mislead future implementation.
2. Active UI still has legacy document fallback paths.
3. User/project scoping is not strict enough in several v3 helper functions.
4. Legacy Notes/Web CRUD still allows unscoped operations.
5. Reference Vault schema is useful but not complete against `_context_packs/data_model_sqlite.md`.
6. Workbench document storage uses `editor_documents`; the v3 context pack recommends `workbench_documents`.
7. `token_usage` still exists while the context pack recommends `usage_logs`; avoid churn unless there is a product need.
8. OpenAlex cache table stores search responses, but there is no active OpenAlex import CRUD into Reference Vault yet.
9. Citation log tables exist, but no backend-verified renderer is active yet.

## Detailed Fix Plan

### Phase 0: Documentation Alignment Only

Goal: remove PostgreSQL confusion without changing runtime behavior.

Changes to make later:

1. Mark `0_change_architecture_report.md` as superseded by the SQLite v3 decision.
2. Update `instructions.md` to stop pointing implementation readers to `_context_packs/data_model_postgresql.md`.
3. Keep `_context_packs/data_model_postgresql.md` only as historical material, or rename/move it into an archive if the team wants.
4. Add a short note in README or architecture docs: SQLite is source of truth; PostgreSQL migration is not planned.

Validation:

- `rg -n "postgres|postgresql|psycopg|DATABASE_URL" AGENTS.md CLAUDE.md README.md instructions.md implementation_prompts reports _context_packs requirements.txt`
- Confirm remaining hits are clearly marked historical/obsolete.

### Phase 1: Lock the SQLite Boundary

Goal: make `database.py` the only runtime SQLite connection path.

Changes to make later:

1. Keep `sqlite3.connect(...)` only inside `get_db_connection()`.
2. Keep test-only raw `sqlite3.connect(...)` acceptable for schema inspection.
3. Add a lightweight static test that scans runtime `.py` files and fails if `sqlite3.connect` appears outside `database.py`.
4. Add a static test that fails if PostgreSQL dependencies or `DATABASE_URL` appear in runtime config.

Validation:

- `rg -n "sqlite3.connect" --glob "*.py"`
- `rg -n "psycopg|postgresql|DATABASE_URL|db_postgres|database_postgres" --glob "*.py" requirements.txt`

### Phase 2: Tighten User and Project Scoping

Goal: make active v3 DB operations safe by default.

Changes to make later in `database.py`:

1. Preserve function names but add optional compatible scoped variants or parameters.
2. Require `user_id` at active call sites for:
   - `update_reference_vault_document_status`
   - `update_reference_vault_document_metadata`
   - `get_reference_vault_chunks_by_document`
   - `update_reference_vault_chunk_vector_id`
   - `delete_reference_vault_chunks_by_document`
3. Keep old optional behavior only where legacy callers require it, and document it as compatibility-only.
4. Validate identifier inputs in `_add_column_if_missing()` before f-string SQL.

Suggested compatibility pattern:

```python
def get_reference_vault_chunks_by_document(
    document_id: str,
    user_id: str = None,
    project_id: str = None,
) -> list:
    ...
```

Then update active v3 callers to pass `user_id` and `project_id`.

Validation:

- Add tests proving user A cannot read/update/delete user B Reference Vault rows.
- Existing tests should continue passing.

### Phase 3: Complete Reference Vault as Active Document Store

Goal: stop active UI ingestion/deletion from depending on legacy `documents` and `parent_chunks`.

Changes to make later:

1. Remove active `app.py` initialization from `database.load_all_documents(user_id)`.
2. Remove left-panel fallback that displays legacy `processed_docs` as active uploaded papers.
3. Route uploaded paper list entirely through `list_reference_vault_documents(user_id)`.
4. Delete/archive uploaded papers through Reference Vault document status, not legacy document delete.
5. Delete Reference Vault chunks through scoped Reference Vault chunk helpers.
6. Keep legacy `documents` and `parent_chunks` tables and CRUD for compatibility, but stop active UI from calling them.

Validation:

- Upload a PDF.
- Confirm row in `reference_vault_documents`.
- Confirm no new row is required in legacy `documents`.
- Confirm deletion archives Reference Vault document instead of deleting legacy metadata.

### Phase 4: Schema Gap Fill, SQLite Only

Goal: align current v3 schema with `_context_packs/data_model_sqlite.md` without replacing existing tables.

Potential columns to add via `_add_column_if_missing()`:

For `reference_vault_documents`:

- `source_origin`
- `source_name`
- `landing_page_url`
- `pdf_url`
- `summary`

For `reference_vault_chunks`:

- `node_metadata_json`

Potential table to add:

- `workbench_documents`, if the product needs to distinguish v3 Workbench documents from legacy `editor_documents`.

Recommended conservative choice:

- Keep `editor_documents` for now because app call sites already depend on it.
- Add `workbench_documents` only when the Workbench migration is implemented.
- Do not rename or drop `editor_documents`.

Validation:

- Run `initialize_database()` against an existing DB and a fresh temp DB.
- Verify old data remains present.
- Verify new columns exist.

### Phase 5: OpenAlex Cache and Import Flow

Goal: keep OpenAlex metadata in SQLite without hardcoding API details.

Changes to make later:

1. Keep `openalex_search_cache` as response cache.
2. Add CRUD for importing a selected OpenAlex work into `reference_vault_documents`.
3. Imported metadata-only records should use:
   - `source_type="openalex"`
   - `status="metadata_only"`
   - no `storage_path`
   - no `docling_markdown_path`
4. Store DOI, OpenAlex ID, title, authors, year, landing page, PDF URL if available.
5. Keep API base URL and key in `.env`.

Validation:

- Import OpenAlex metadata without PDF.
- Confirm it appears in Reference Vault.
- Confirm it is not retrievable/citable as full-text evidence until full text exists.

### Phase 6: Citation Tables and Renderer

Goal: ensure citations are rendered from verified SQLite metadata, not invented by the LLM.

Changes to make later:

1. Add a backend helper that receives allowed `document_id` / `chunk_id` values.
2. Verify each candidate against `reference_vault_documents` and `reference_vault_chunks`.
3. Render final text as `[author_name, paper_name]`.
4. Save `citation_candidates` and `citation_logs`.
5. Ensure OpenThaiGPT selects IDs only, not final citation strings.

Validation:

- Test missing document ID is rejected.
- Test metadata-only OpenAlex record is not used as evidence-backed citation.
- Test final string exactly matches `[author_name, paper_name]` format.

### Phase 7: Legacy Notes and Web Deactivation

Goal: remove Notes/Web from active product scope without deleting legacy data.

Changes to make later:

1. Keep legacy tables:
   - `research_notes`
   - `web_pages`
2. Keep legacy CRUD functions temporarily to avoid import breakage.
3. Remove active UI calls to:
   - `save_note`
   - `load_all_notes`
   - `delete_note_by_id`
   - `save_web_page`
   - `load_all_web_pages`
   - `delete_web_page_by_id`
4. Stop writing Notes/Web vectors to Pinecone.
5. Add comments/docstrings marking those functions compatibility-only.

Validation:

- `rg -n "save_note|load_all_notes|delete_note_by_id|save_web_page|load_all_web_pages|delete_web_page_by_id" app.py`
- Result should be empty or in unreachable legacy/archive code only.

### Phase 8: Test Coverage

Add or extend tests for:

1. SQLite-only runtime boundary.
2. No PostgreSQL runtime dependencies.
3. Reference Vault schema creation on fresh DB.
4. Existing DB migration preserves legacy data.
5. User-scoped Reference Vault document access.
6. User-scoped Reference Vault chunk access.
7. OpenAlex metadata-only import.
8. Citation renderer with verified metadata.
9. Active upload writes Reference Vault rows and does not require legacy `documents`.
10. Notes/Web functions are not called by active UI.

## Recommended Implementation Order

1. Documentation alignment: remove PostgreSQL confusion.
2. Static guard tests: enforce SQLite-only runtime.
3. User/project scoping fixes in `database.py`.
4. Reference Vault active document-store cleanup in `app.py`.
5. Schema gap fill with additive SQLite migrations only.
6. OpenAlex import CRUD into Reference Vault.
7. Citation renderer backed by SQLite.
8. Legacy Notes/Web active-path removal.

## Commands Used For This Review

```powershell
rg -n "postgres|postgresql|psycopg|pg_|DATABASE_URL|sqlite3|sqlite|connect\(|get_db_connection|initialize_database|from database import|import database|database\.|research_notes.db|Database/" -S .
rg -n "database\." app.py auth.py citation_generator.py docling_ingestion.py llamaindex_pinecone_rag.py vector_store.py benchmark.py tests\test_database_reference_vault_schema.py tests\test_docling_ingestion.py tests\test_phase05_llamaindex_pinecone_indexing.py
rg -n "sqlite3|get_db_connection|DB_PATH|initialize_database|CREATE TABLE|ALTER TABLE|DELETE FROM|INSERT|UPDATE|SELECT" database.py tests\test_database_reference_vault_schema.py tests\test_docling_ingestion.py tests\test_phase05_llamaindex_pinecone_indexing.py
rg -n "postgres|postgresql|psycopg|DATABASE_URL|db_postgres|data_model_postgresql" AGENTS.md CLAUDE.md README.md instructions.md 0_change_architecture_report.md implementation_prompts reports _context_packs requirements.txt
rg -n "psycopg|postgres|sqlite|sqlalchemy|alembic|pinecone|chromadb|chroma" requirements.txt
```

## Verification Status

This was a review and planning task only.

Completed:

- Scanned current DB usage.
- Confirmed runtime code is SQLite-based, not PostgreSQL-based.
- Identified active DB call sites.
- Created this plan at `reports/db_fix.md`.

Not done:

- No product code edits.
- No migrations.
- No tests run.
- No database file modification.
