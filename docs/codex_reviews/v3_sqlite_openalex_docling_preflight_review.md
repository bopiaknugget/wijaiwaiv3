# Wijaiwai v3 SQLite/OpenAlex/Docling Preflight Review

Date: 2026-05-02

Scope: Phase 00 review only. No product refactor was performed.

Target architecture: SQLite source of truth, Reference Vault-only sources, OpenAlex metadata discovery/import, Docling PDF-to-Markdown ingestion, LlamaIndex + Pinecone RAG, OpenThaiGPT Workbench citation reasoning, backend-rendered citations in `[author_name, paper_name]` format, and 3-panel UI: left Reference Vault, middle Workbench, right Chat Assistant.

## 1. Current Architecture Detected From Code

- `app.py` is the active Streamlit application and still describes itself as a "Research Workbench" with a 3-panel layout, but the left panel is implemented as sidebar tabs for `Documents`, `Notes`, and `Web`, not a v3 Reference Vault.
- The current active flow is still "uploaded document / note / web page -> LangChain document/chunk preparation -> SQLite metadata/parent chunks -> Pinecone vectors -> OpenThaiGPT generation".
- `database.py` is imported directly by the UI and utility modules and creates the SQLite database on import.
- `document_loader.py` handles upload parsing and metadata enrichment with LangChain loaders and heuristics.
- `vector_store.py` owns Pinecone client/index access, Pinecone Inference embeddings, upsert, retrieval, fallback retrieval, and deletion.
- `citation_generator.py` owns the active Workbench auto-citation path and calls OpenThaiGPT to insert citation text.
- `generator.py`, `reviewer.py`, `document_loader.py`, `query_router.py`, and `web_scraper.py` call OpenThaiGPT directly.
- No active OpenAlex client/module was detected in product code.
- No active Docling or LlamaIndex usage was detected in product code.

## 2. SQLite Usage

- SQLite is already the source of truth for local app metadata and editor documents.
- `database.py` uses `DB_PATH = Path(__file__).parent / "Database" / "research_notes.db"` and `sqlite3.connect(str(DB_PATH))`.
- Existing tables are legacy-oriented:
  - `research_notes`
  - `documents`
  - `parent_chunks`
  - `web_pages`
  - `token_usage`
  - `users`
  - `editor_documents`
- Startup migrations add `user_id` to `documents`, `research_notes`, and `web_pages`.
- The v3 Reference Vault tables are not implemented yet:
  - `projects`
  - `reference_vault_documents`
  - `reference_vault_chunks`
  - `workbench_documents`
  - `openalex_search_cache`
  - `citation_candidates`
  - `citation_logs`
  - `usage_logs`
- The current `documents` table stores upload metadata but is not rich enough for v3 paper metadata, OpenAlex IDs, Docling artifacts, or citation-safe vault records.
- `parent_chunks` stores larger context chunks in SQLite, while child chunks live in Pinecone metadata/vectors.

## 3. Upload / Document Parser Flow

Current active upload flow in `app.py`:

```text
st.file_uploader(type=["pdf", "txt", "docx", "doc"])
  -> temp file
  -> document_loader.load_document()
  -> document_loader.enrich_metadata(..., source_type="document")
  -> document_loader.create_parent_child_chunks(..., source_type="document")
  -> document_loader.create_summary_documents()
  -> database.save_document_metadata(..., db_path="pinecone")
  -> vector_store.ingest_documents()
```

Parser details:

- `document_loader.load_document()` supports `.pdf`, `.txt`, `.docx`, and `.doc`.
- PDFs are parsed through `PyPDFLoader`.
- TXT is parsed through `TextLoader` with UTF-8 and CP874 fallback.
- DOC/DOCX is parsed through `Docx2txtLoader`.
- Metadata extraction is heuristic: title from first line, year regex, author heuristic, then optional direct OpenThaiGPT author extraction fallback.
- No Docling import or Docling conversion path was found.
- No Markdown artifact storage path exists for `storage/reference_vault/{user_id}/{project_id}/{document_id}/docling.md`.
- Current ingestion deletes the temporary original file after parsing, so v3 original-file storage is not yet present.

Gap against v3:

- Replace the PDF/scanned PDF parser path with Docling-to-Markdown.
- Store original file, Docling Markdown, and Docling metadata artifacts.
- Restrict active upload ingestion to paper/reference documents.
- Preserve TXT/DOC/DOCX only if the product explicitly treats them as supported Reference Vault papers; otherwise hide/remove from active v3 upload.

## 4. Pinecone / RAG Flow

Current Pinecone flow:

- `vector_store.py` loads `.env`, reads `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, and `PINECONE_HOST`.
- Pinecone client is created with `Pinecone(api_key=PINECONE_API_KEY)`.
- Pinecone index is opened with `pc.Index(PINECONE_INDEX_NAME, host=PINECONE_HOST)` when host exists.
- Embeddings are generated through Pinecone Inference model `multilingual-e5-large`.
- `upsert_documents()` stores `content` in Pinecone metadata and defaults missing `source_type` to `document`.
- Vectors are upserted into `namespace=user_id`.
- `retrieve_unified()` queries `namespace=user_id`, supports `source_type` and `doc_name` filters, does parent expansion from SQLite, hybrid BM25/vector scoring, and context capping.
- `enhanced_retrieve()` wraps `retrieve_unified()` with query classification, reranking, and fallback.

Important v3 risks:

- Pinecone namespace is user-scoped, but v3 metadata should also include `project_id`, `document_id`, `chunk_id`, and `source_type="reference_document"`.
- Current active source types include `document`, `note`, and `web_page`.
- `enhanced_retrieve()` retries without filters when filtered retrieval returns empty, which can broaden retrieval beyond v3 Reference Vault constraints.
- Pinecone metadata currently carries source text and legacy fields; SQLite is not used as a strict verification gate before LLM/citation output.
- LlamaIndex is not currently used for chunking/indexing/retrieval orchestration.

## 5. Citation Generation Flow

Current active citation path:

```text
app.py Auto Citation expander
  -> citation_generator.generate_citation_output(editor_text, user_id)
  -> extract_paragraph_citations()
  -> retrieve_unified(..., source_type="document", return_scores=True)
  -> OpenThaiGPT prompt asks model to insert [Author, Year]
  -> validation strips unknown bracket patterns
  -> fallback appends [Author, Year]
  -> APA7 reference markdown rendered in app.py
```

Current behavior:

- Citation prompt explicitly asks the LLM to insert `[Author, Year]`.
- `format_apa7_in_text()` returns `(Author, Year)`.
- Fallback converts `(Author, Year)` to `[Author, Year]`.
- `format_apa7_reference()` builds APA-like reference entries.
- The LLM writes the final inline citation text, with partial post-validation.

Gap against v3:

- Target citation format is `[author_name, paper_name]`, not `[Author, Year]`.
- OpenThaiGPT should select allowed citation IDs only.
- Backend must validate selected IDs against SQLite Reference Vault metadata.
- Backend must render final citation text from verified SQLite metadata.
- Current code can cite retrieved Pinecone metadata without a Reference Vault table verification step.

## 6. OpenThaiGPT Call Paths

Detected direct OpenThaiGPT paths:

- `generator.py`
  - `API_URL` is hardcoded to `http://thaillm.or.th/api/openthaigpt/v1/chat/completions`.
  - `MODEL = "/model"`.
  - `_call_api()` uses non-streaming JSON payload.
  - `_call_api_stream()` sends compact JSON with `"stream":true` on the wire.
  - Multiple generation/edit/research helpers read `OPENTHAI_API_KEY` from `.env`.
- `reviewer.py`
  - Uses the same hardcoded OpenThaiGPT URL and model.
  - Has streaming and non-streaming API helpers.
- `citation_generator.py`
  - Imports `generator._call_api` for LLM-powered citation insertion.
- `document_loader.py`
  - Calls OpenThaiGPT directly for author-name extraction fallback.
- `query_router.py`
  - Can call OpenThaiGPT for optional LLM query classification fallback.
- `web_scraper.py`
  - Calls OpenThaiGPT for web summary/title generation.

Config gap:

- `.env` contains the relevant variable names for OpenThaiGPT and OpenAlex, but product code still hardcodes OpenThaiGPT URL in several files instead of centralizing configurable endpoint access.
- OpenThaiGPT should remain for Workbench reasoning, but citation rendering must move out of the LLM response.

## 7. Notes / Web Active Paths

Notes are active:

- `app.py` renders a `Notes` tab.
- The Notes tab calls `database.save_note()`, `database.load_all_notes()`, and `database.delete_note_by_id()`.
- Notes are embedded into Pinecone through `vector_store.ingest_note()`.
- `vector_store.ingest_note()` stores `source_type="note"`.

Web scraping is active:

- `app.py` imports `scrape_url`, `summarize_content`, `generate_title`, and `prepare_web_chunks` from `web_scraper.py`.
- `app.py` renders a `Web` tab.
- The Web tab scrapes a URL, summarizes/title-generates via OpenThaiGPT, stores a `web_pages` row, chunks the summary, and indexes it into Pinecone.
- Web deletion calls `delete_document()`, `database.delete_parent_chunks_by_source()`, and `database.delete_web_page_by_id()`.
- `web_scraper.py` is a full active module with scraping, summarization, title generation, and chunk preparation.

Gap against v3:

- Notes and Web are explicitly removed from active product scope.
- They may remain as legacy tables/modules, but must not be reachable from active UI, active ingestion, active retrieval, or active citation paths.

## 8. Env / Config Usage

Detected env/config behavior:

- `vector_store.py` reads Pinecone config from `.env`:
  - `PINECONE_API_KEY`
  - `PINECONE_INDEX_NAME`
  - `PINECONE_HOST`
- `generator.py`, `reviewer.py`, `citation_generator.py`, `document_loader.py`, `query_router.py`, and `web_scraper.py` read `OPENTHAI_API_KEY`.
- `README.md` documents `OPEN_ALEX_API_KEY` and `OPEN_ALEX_BASE_URL`.
- `.env` includes OpenAlex variable names, but no code path reads `OPEN_ALEX_API_KEY` or `OPEN_ALEX_BASE_URL`.
- `requirements.txt` includes LangChain, Pinecone, PyPDF, docx2txt, requests, dotenv, Streamlit, web scraping packages, OAuth, Redis, and limits.
- `requirements.txt` does not include Docling or LlamaIndex dependencies.
- Hardcoded endpoint risk remains in OpenThaiGPT call files.

Security note:

- The review did not copy secret values into this report. Future implementation should avoid committing real API keys in `.env` and should keep `.env` ignored.

## 9. Files That Need Changes Per Phase

Phase 01 / product scope and UI shell:

- `app.py`
  - Rename/reshape left panel to Reference Vault.
  - Hide/remove active Notes tab.
  - Hide/remove active Web tab.
  - Preserve 3-panel layout: left Reference Vault, middle Workbench, right Chat Assistant.

Phase 02 / SQLite Reference Vault schema:

- `database.py`
  - Add v3 tables while keeping SQLite.
  - Add Reference Vault document/chunk functions.
  - Add OpenAlex cache/import functions.
  - Add citation candidate/log functions.
  - Keep legacy tables only for compatibility, not active v3 flows.

Phase 03 / OpenAlex integration:

- Add an OpenAlex client module, likely `openalex_client.py` or similar.
- Add functions expected by context:
  - `search_works(query, per_page=10)`
  - `get_work_by_doi(doi)`
  - `normalize_work(raw_work)`
  - `cache_openalex_search(query, response)`
  - `import_openalex_work_to_reference_vault(...)`
- Update `database.py` for cache/import persistence if not already done in Phase 02.
- Update `app.py` left panel to support OpenAlex search/import.

Phase 04 / Docling ingestion:

- `document_loader.py`
  - Add or route PDF upload through Docling conversion.
  - Store original PDF, Docling Markdown, and metadata artifacts.
  - Return Markdown-backed document/chunk inputs for indexing.
- `requirements.txt`
  - Add Docling dependency.
- `database.py`
  - Persist Docling status and artifact paths.

Phase 05 / LlamaIndex + Pinecone indexing:

- `vector_store.py`
  - Keep Pinecone client/index functions where useful.
  - Replace legacy LangChain chunk/index orchestration with LlamaIndex where required.
  - Use v3 metadata: `user_id`, `project_id`, `document_id`, `chunk_id`, `source_type="reference_document"`.
  - Prevent Notes/Web retrieval fallback in v3 paths.
- `document_loader.py` or a new ingestion service
  - Convert Docling Markdown into LlamaIndex nodes/chunks.
- `requirements.txt`
  - Add LlamaIndex dependencies.

Phase 06 / Reference Vault retrieval rules:

- `vector_store.py`
  - Add strict Reference Vault retrieval function.
  - Verify retrieved chunks/documents against SQLite.
  - Remove broad fallback for citation/workbench flows.
- `app.py`
  - Route Chat Assistant and Workbench citation retrieval through Reference Vault-only paths.

Phase 07 / Reference Vault left panel:

- `app.py`
  - Direct paper upload UI.
  - OpenAlex search/import UI.
  - Vault paper list.
  - Processing status and metadata-only warnings.

Phase 08 / Workbench citation service:

- `citation_generator.py`
  - Replace LLM-written citation strings with ID-selection flow.
  - Build allowed citation candidates from verified SQLite Reference Vault metadata.
  - Ask OpenThaiGPT to return JSON citation IDs only.
  - Validate IDs.
  - Render `[author_name, paper_name]` in backend code.
- `app.py`
  - Update UI labels away from APA7 and Knowledge Base terminology.

Phase 09 / cleanup and config hardening:

- `web_scraper.py`
  - Keep only if archived/unused, or remove from active imports.
- `requirements.txt`
  - Remove web scraping dependencies if no longer used.
- `generator.py`, `reviewer.py`, `document_loader.py`, `query_router.py`
  - Centralize OpenThaiGPT config where possible.
- `.env.example` or docs
  - Document required variable names without secrets.

## 10. Diff-Style Plan: Keep / Remove-Hide / Replace / Add

Keep:

- SQLite as local source of truth.
- Existing Streamlit app as the smallest-safe UI shell.
- Existing Workbench editor and Chat Assistant panels.
- Existing Pinecone connection helpers where compatible.
- Existing OpenThaiGPT generation/reviewer call paths where compatible.
- Existing `user_id` scoping as a baseline.
- Existing editor document persistence as `workbench_documents` migration input.

Remove-hide:

- Active Notes tab in `app.py`.
- Active Web tab in `app.py`.
- `web_scraper` import from active app path.
- `ingest_note()` usage from active app path.
- Notes/Web source types from active retrieval/citation paths.
- Web scraping dependencies from active requirements after removal is complete.
- APA7 UI labels and Knowledge Base terminology where Reference Vault is intended.

Replace:

- `Documents` left-tab model with `Reference Vault`.
- `PyPDFLoader` PDF ingestion with Docling PDF/scanned PDF-to-Markdown ingestion.
- LangChain-only chunk orchestration with LlamaIndex nodes/chunks for v3 RAG.
- Pinecone metadata `source_type="document"` with `source_type="reference_document"` in v3 paths.
- LLM-written `[Author, Year]`/APA citation generation with backend-rendered `[author_name, paper_name]`.
- Broad retrieval fallback in citation/workbench paths with strict Reference Vault-only retrieval.

Add:

- SQLite Reference Vault tables and functions.
- OpenAlex client and cache/import layer.
- Docling artifact storage.
- LlamaIndex indexing/retrieval orchestration.
- Citation candidate/log tables.
- Workbench citation guard/service that validates candidate IDs and renders final citations.
- `.env.example` or setup docs listing required variable names only.

## 11. Risks and Minimal Order

Primary risks:

- Citation trust risk: current citation flow lets OpenThaiGPT produce final citation strings and still targets `[Author, Year]`, which violates v3.
- Scope risk: active Notes and Web paths can still enter Pinecone and retrieval, violating Reference Vault-only source rules.
- Retrieval risk: fallback retrieval can broaden searches beyond source filters.
- Data model risk: current `documents` and `parent_chunks` tables cannot represent v3 OpenAlex metadata, Docling artifacts, metadata-only records, chunk IDs, or citation logs.
- Ingestion risk: Docling/LlamaIndex are not installed or wired, so PDF/scanned PDF ingestion is still legacy.
- Config risk: OpenThaiGPT endpoint is hardcoded in multiple modules.
- Migration risk: changing ingestion, retrieval, UI, and citation in one pass would be high blast radius.

Minimal safe order:

1. Add SQLite Reference Vault schema and compatibility functions without deleting legacy tables.
2. Hide/remove active Notes and Web from UI and active ingestion/retrieval paths.
3. Add OpenAlex metadata search/import into Reference Vault.
4. Add Docling upload ingestion and artifact storage for uploaded papers.
5. Add LlamaIndex + Pinecone indexing for Reference Vault chunks with strict v3 metadata.
6. Add strict Reference Vault retrieval that verifies Pinecone results against SQLite.
7. Replace citation generation with OpenThaiGPT ID selection and backend-rendered `[author_name, paper_name]`.
8. Update labels/docs/config after behavior is in place.

Recommended first implementation phase:

- Start with SQLite Reference Vault schema because it becomes the verification boundary for OpenAlex import, Docling ingestion, strict retrieval, and citation rendering.
- Keep legacy tables intact during the transition.
- Do not migrate to PostgreSQL.

