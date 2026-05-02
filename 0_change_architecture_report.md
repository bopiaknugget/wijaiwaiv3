# Wijaiwai Architecture Change Report

Review scope: current codebase compared against the v2 architecture only. No product code was refactored.

## 1. Current Architecture Summary

### Application shape

- Primary app entry point is `app.py`, a Streamlit UI.
- The UI is still organized as a sidebar plus two main columns:
  - sidebar: Documents, Notes, Web tabs (`app.py:838-839`)
  - center: Research Workbench (`app.py:1213-1217`)
  - right: assistant chat and research actions
- There is also a CLI path in `main.py` for ingesting a PDF and querying RAG (`main.py:33-55`, `main.py:72-93`).

### Storage

- Main persistent database is SQLite via `database.py`.
- SQLite file path is hardcoded as `Database/research_notes.db` (`database.py:16`).
- Tables currently created:
  - `research_notes` (`database.py:49`)
  - `documents` (`database.py:59`)
  - `parent_chunks` (`database.py:72`)
  - `web_pages` (`database.py:84`)
  - `token_usage` (`database.py:96`)
  - `users` (`database.py:108`)
  - `editor_documents` (`database.py:487`)
- Existing ownership scope is mostly `user_id`, not `project_id`.
- There is no PostgreSQL connection layer, migration system, `DATABASE_URL`, or PostgreSQL dependency in `requirements.txt`.

### Document upload and RAG

- Document upload happens in the sidebar Documents tab (`app.py:842-982`).
- Upload supports PDF, TXT, DOCX, DOC (`app.py:845-849`).
- Processing flow:
  - load document via `document_loader.load_document`
  - enrich metadata via `enrich_metadata`
  - create parent-child chunks
  - create summary documents
  - save document metadata in SQLite
  - ingest chunks into Pinecone (`app.py:911-958`)
- Parent chunks are stored in SQLite before vectors are upserted to Pinecone (`vector_store.py:374-376`).
- Pinecone namespace is currently `user_id` (`vector_store.py:352`, `vector_store.py:568-570`).
- Retrieval supports source and document metadata filters (`vector_store.py:488-553`) and hybrid reranking/BM25-like scoring in code.

### Active Notes and Web scope

- Notes are still active in UI and backend:
  - Notes tab in Streamlit (`app.py:1011-1081`)
  - `database.save_note`, `load_all_notes`, `delete_note_by_id` (`database.py:137-177`)
  - note ingestion to Pinecone as `source_type='note'` (`vector_store.py:388-417`)
- Web scraping is still active:
  - Web tab in Streamlit (`app.py:1083-1205`)
  - `web_scraper` imported in app (`app.py:47`)
  - SQLite web page persistence (`database.py:319-407`)
  - web content ingested into Pinecone (`app.py:1136-1157`)

### LLM

- OpenThaiGPT is already the main LLM API path in `generator.py` and `reviewer.py`.
- The app uses `OPENTHAI_API_KEY`, hardcoded OpenThaiGPT URL, and model `"/model"`.
- Workbench review uses `reviewer.review_research` (`app.py:1593-1601`).
- Chat/research answer generation uses `generator.generate_answer` through assistant flows.

### Citation behavior

- Current auto-citation module is `citation_generator.py`.
- It retrieves matching document chunks and inserts citations using OpenThaiGPT.
- Current citation format is APA-like `[Author, Year]`, not the new `[author_name, paper_name]` format.
- Current citation matching is Pinecone-driven and validates LLM edits structurally, but does not verify final citations against PostgreSQL/Vault records because those records do not exist yet.

## 2. Gap Analysis vs New Architecture

### Requirement 1: Replace SQLite with PostgreSQL

Current gap:

- SQLite is the active source of truth (`database.py:16`, `database.py:31`).
- All app persistence functions are SQLite-specific.
- Parent chunks, users, documents, workbench documents, notes, web pages, and usage logs are in one SQLite module.
- No `project_id` enforcement exists in database queries.
- No PostgreSQL tables exist for `reference_documents`, `reference_chunks`, `workbench_analysis_runs`, `citation_candidates`, `citation_logs`, or `openalex_search_cache`.

Smallest safe migration path:

- Keep `database.py` function names temporarily, but swap internals behind a PostgreSQL adapter.
- Add PostgreSQL schema/migration first.
- Migrate only in-scope tables first: users, projects, reference documents, chunks, workbench documents, usage logs.
- Archive or stop writing Notes/Web data rather than migrating those modules as active product features.

### Requirement 2: Add OpenAlex paper/document metadata discovery

Current gap:

- No OpenAlex module or API flow exists.
- No `OPENALEX_BASE_URL` config exists.
- No OpenAlex cache/import table exists.
- Uploaded document metadata extraction is heuristic and LLM-assisted only (`document_loader.py`).

Smallest safe migration path:

- Add `openalex_client.py` with search, DOI lookup, normalization, and timeout handling.
- Add PostgreSQL `openalex_search_cache`.
- Add OpenAlex UI entry point under Reference Vault, clearly labeled metadata-only.
- Use OpenAlex for discovery and optional metadata enrichment, not direct citation.

### Requirement 3: Remove Notes and Web Scraping from active product scope

Current gap:

- Notes and Web tabs are still visible and functional (`app.py:838-839`, `app.py:1011-1205`).
- Notes and web content can be embedded into Pinecone and retrieved.
- Retrieval supports `source_type='note'` and `source_type='web_page'` (`vector_store.py:510`, `vector_store.py:828`).

Smallest safe migration path:

- Hide/remove Notes and Web tabs from active UI.
- Stop importing `web_scraper` in `app.py`.
- Stop calling `ingest_note` and web ingestion from active app flows.
- Keep old SQLite functions temporarily only for migration/export compatibility, but do not expose them.
- Restrict Workbench citation retrieval to `source_type='document'` or new `source_type='reference_document'`.

### Requirement 4: Rename uploaded documents area to Reference Vault

Current gap:

- UI labels still say `Documents`, `Upload Documents`, `Process Documents`, `Knowledge Base`, and document-oriented wording (`app.py:838-856`, `app.py:1617-1622`).
- Database table is still named `documents`, not `reference_documents`.
- Metadata names are still `doc_name`, `paper_title`, `authors`, and `year` in vector metadata.

Smallest safe migration path:

- First rename UI labels only:
  - Documents -> Reference Vault
  - Upload Documents -> Upload to Reference Vault
  - Knowledge Base -> Reference Vault
- Then map SQLite/PostgreSQL `documents` to `reference_documents`.
- Preserve existing function names during first migration, adding wrapper names only after tests pass.

### Requirement 5: Workbench must analyze text and cite Vault papers as `[author_name, paper_name]` using OpenThaiGPT

Current gap:

- Existing citation output is `[Author, Year]` (`citation_generator.py`).
- Citation candidates are not represented with stable `citation_id`, `document_id`, `chunk_ids`, and `evidence_excerpt`.
- The LLM is allowed to insert citation text directly, then local code strips unknown citations. New architecture wants backend-rendered citations after LLM selects allowed citation IDs.
- Retrieval is scoped by Pinecone namespace `user_id`, not verified against PostgreSQL `user_id + project_id`.
- Current `enhanced_retrieve` can retry without filters if a filtered retrieval is empty (`vector_store.py:880-889`). That is useful for chat but risky for strict Vault citation because fallback could broaden beyond intended source filters if not constrained.

Smallest safe migration path:

- Add a new Workbench citation service instead of changing all chat generation:
  - retrieve candidate chunks from Reference Vault only
  - verify chunks/documents in PostgreSQL by `user_id` and `project_id`
  - build allowed candidates
  - call OpenThaiGPT with structured prompt
  - accept citation IDs only
  - render `[author_display, paper_name]` in backend
  - log run and citations
- Keep existing chat/review flows until the citation service is stable.

## 3. Files That Need Changes

### High priority

- `database.py`
  - Replace SQLite internals with PostgreSQL adapter or split into `db_postgres.py`.
  - Add project-scoped queries.
  - Add Reference Vault, workbench, analysis, citation, OpenAlex cache tables.

- `requirements.txt`
  - Add PostgreSQL driver/migration dependencies such as `psycopg[binary]` or `psycopg2-binary`.
  - Remove web scraping dependencies after Web scope is removed, if no longer used elsewhere.

- `app.py`
  - Rename Documents UI to Reference Vault.
  - Remove/hide Notes and Web tabs.
  - Add OpenAlex search UI.
  - Route Workbench citation analysis to new Vault citation flow.
  - Replace `Knowledge Base` user-facing labels with `Reference Vault`.

- `document_loader.py`
  - Preserve extraction/chunking, but enrich metadata to match `reference_documents` fields.
  - Add `document_id`, `project_id`, `author_display`, `paper_name`, DOI/OpenAlex metadata when available.

- `vector_store.py`
  - Store vector IDs and chunk metadata in PostgreSQL.
  - Scope retrieval by `user_id + project_id`.
  - Restrict citation retrieval to Reference Vault source type.
  - Avoid broad fallback for citation-specific retrieval.
  - Remove active note/web ingestion paths or mark legacy.

- `citation_generator.py`
  - Replace APA `[Author, Year]` flow for Workbench with `[author_name, paper_name]`.
  - Introduce allowed citation candidates and backend citation verification.
  - Keep old APA export only if explicitly retained as separate export feature.

### New files likely needed

- `db_postgres.py` or `database_postgres.py`
  - PostgreSQL connection, row helpers, transaction helpers.

- `migrations/001_initial_postgresql.sql`
  - Creates users, projects, reference documents/chunks, workbench documents, analysis runs, citation candidates/logs, usage logs, OpenAlex cache.

- `openalex_client.py`
  - Search, DOI lookup, result normalization, error handling.

- `workbench_citation_service.py`
  - Claim/citation analysis orchestration with OpenThaiGPT and verification.

- `reference_vault_service.py`
  - Upload metadata persistence, duplicate checks, eligibility status.

### Medium priority / cleanup

- `auth.py`
  - `database.save_user` currently writes to SQLite (`auth.py:132-133`); route to PostgreSQL.

- `main.py`
  - CLI ingest/query should include project scope or be marked dev-only.

- `web_scraper.py`
  - Remove from active app imports. Archive only if needed.

- tests under `tests/` and root test files
  - Add PostgreSQL data-layer tests and citation verification tests.
  - Update tests that assume Notes/Web are active.

## 4. Minimal Refactor Plan

### Phase 1: Stabilize boundaries without feature rewrite

1. Add PostgreSQL config and connection helper.
2. Add migrations for the v2 schema.
3. Add project concept with a default project for current single-user flows.
4. Keep existing function names in `database.py` where possible, but route new in-scope calls to PostgreSQL.
5. Do not migrate Notes/Web into active v2 schema.

### Phase 2: Reference Vault migration

1. Rename UI labels from Documents/Knowledge Base to Reference Vault.
2. Save uploads to `reference_documents`.
3. Save chunks to `reference_chunks`.
4. Store vector IDs returned from Pinecone in PostgreSQL.
5. Keep Pinecone as retrieval index only; verify all results against PostgreSQL before citation.

### Phase 3: Remove active Notes/Web scope

1. Remove Notes and Web tabs from `app.py`.
2. Remove web scraping import from active app path.
3. Disable note/web ingestion to Pinecone.
4. Keep legacy functions temporarily only to avoid breaking import-time code and old data export.

### Phase 4: OpenAlex metadata discovery

1. Add `openalex_client.py`.
2. Add search/cache functions in the data layer.
3. Add small OpenAlex UI under Reference Vault.
4. Add metadata-only warnings.
5. Optional: support metadata enrichment when an uploaded Vault document matches DOI/title.

### Phase 5: Workbench citation analysis

1. Add `workbench_citation_service.py`.
2. Retrieve Vault chunks only.
3. Build `citation_candidates`.
4. Call OpenThaiGPT with allowed citation IDs.
5. Verify all returned IDs against PostgreSQL.
6. Render final citations as `[author_display, paper_name]`.
7. Log analysis runs and citation logs.

## 5. Risk Assessment

### High risk

- Database migration risk: current SQLite module is imported directly by auth, app, vector store, citation generation, and tests.
- Citation trust risk: current LLM citation flow inserts free-form citation text; new flow requires citation IDs and backend rendering.
- Data isolation risk: current namespace isolation is `user_id` only; v2 requires `user_id + project_id`.
- Retrieval fallback risk: broad fallback in `enhanced_retrieve` is helpful for chat but unsafe for strict Vault-only citation if reused unchanged.

### Medium risk

- UI churn risk: `app.py` is large and contains Documents, Notes, Web, Workbench, review, citation, and assistant logic in one file.
- Metadata quality risk: current metadata extraction is heuristic and can produce weak title/author data.
- Pinecone delete risk: current delete logic depends on namespace and metadata/prefix behavior, not PostgreSQL-owned vector records.
- OpenAlex ambiguity risk: title/author matching can create false metadata links unless DOI or high-confidence matching is used.

### Low risk

- Renaming UI labels to Reference Vault is low risk if done before deeper schema migration.
- Adding OpenAlex search as metadata-only is low risk if it is not mixed into citation retrieval.
- Keeping current OpenThaiGPT API path is low risk because it already exists.

## 6. Suggested Implementation Order

1. Add PostgreSQL schema and connection layer.
2. Add default project handling and enforce `user_id + project_id` in new data paths.
3. Rename Documents/Knowledge Base UI to Reference Vault.
4. Move upload metadata and chunk metadata from SQLite to PostgreSQL.
5. Store Pinecone vector IDs in PostgreSQL and verify retrieval results against PostgreSQL.
6. Remove/hide Notes and Web from active UI and retrieval.
7. Add OpenAlex metadata search and cache.
8. Add Workbench citation service with allowed citation candidates.
9. Change final Workbench citation format to `[author_display, paper_name]`.
10. Add citation logs and verification failure fallback.
11. Update tests for PostgreSQL, Reference Vault retrieval, OpenAlex normalization, and citation verification.

## Smallest Safe Migration Path

The safest path is not a full rewrite. Keep the Streamlit app and existing upload/chunk/retrieval functions initially, but replace the persistence and citation boundaries underneath them:

```text
SQLite documents -> PostgreSQL reference_documents
SQLite parent_chunks -> PostgreSQL reference_chunks
Pinecone namespace user_id -> user_id + project_id verified by PostgreSQL
Documents UI -> Reference Vault UI
APA citation helper -> Vault citation candidate service
Notes/Web UI -> removed from active product
OpenAlex -> metadata-only discovery/cache
```

This keeps the current MVP usable while moving the architecture toward PostgreSQL-backed, Vault-only, citation-verified Workbench analysis.
