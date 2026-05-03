# Phase 05 LlamaIndex + Pinecone Indexing Implementation Plan

## Executive Summary

Phase 05 should add the missing Reference Vault indexing path between Docling PDF-to-Markdown ingestion and Pinecone-backed retrieval. The repository now has much of the upstream foundation: PDF-only Reference Vault upload, Docling artifact storage, Reference Vault SQLite document/chunk tables, and LlamaIndex/Pinecone dependencies. The core gap is that converted Markdown is not yet chunked into LlamaIndex nodes, persisted as SQLite chunks, or indexed into Pinecone with the required Reference Vault metadata.

The smallest safe implementation is to add a narrow LlamaIndex/Pinecone service for Reference Vault documents, wire it only into the successful Docling upload path, and keep existing legacy retrieval functions as compatibility wrappers while v3 flows move to strict Reference Vault-only retrieval. SQLite must remain the source of truth; Pinecone must remain a vector index only.

## Prompt Expectations

Source prompt: `implementation_prompts\phase_05_llamaindex_pinecone_indexing.md`.

Phase 05 requires:

- Add a Reference Vault indexing path using LlamaIndex + Pinecone.
- Required vector metadata:
  - `user_id`
  - `project_id`
  - `document_id`
  - `chunk_id`
  - `source_type=reference_document`
- Save chunks and Pinecone vector IDs in SQLite.
- Do not index Notes or Web.
- Preserve global v3 decisions:
  - SQLite source of truth.
  - Pinecone vector index only.
  - Docling PDF/scanned PDF-to-Markdown upstream.
  - LlamaIndex for chunking, indexing, and retrieval orchestration.
  - No fabricated citations.
  - Reference Vault only for evidence-backed RAG.

## Current Implementation State

### Dependency State

- `requirements.txt:15` includes `pinecone>=5.0.0`.
- `requirements.txt:41` includes `docling>=2.68.0`.
- `requirements.txt:44-46` include `llama-index`, `llama-index-vector-stores-pinecone`, and `llama-index-readers-docling`.
- Legacy LangChain dependencies remain at `requirements.txt:10-12`; these are still used by current `vector_store.py`, `document_loader.py`, and legacy RAG paths.
- Web scraping dependencies still remain at `requirements.txt:30-31`; this is outside Phase 05 but matters because Notes/Web must not enter v3 indexing/retrieval.

### Docling Upload State

- `app.py:19` imports `ingest_uploaded_pdf_with_docling`.
- `app.py:794` uses a PDF-only upload widget for the Reference Vault path.
- `app.py:843` calls `ingest_uploaded_pdf_with_docling(...)` after upload validation.
- `app.py:869` explicitly reports: indexing is pending for the next phase.
- `docling_ingestion.py:22` stores artifacts under `storage/reference_vault`.
- `docling_ingestion.py:79` defines `ingest_uploaded_pdf_with_docling(...)`.
- `docling_ingestion.py:133` writes `index_status="pending"` into metadata.
- `docling_ingestion.py:139` writes the converted Markdown artifact.
- `docling_ingestion.py:153-155` marks successful documents as `docling_status="succeeded"` and `status="active"`.

Current reality: Docling conversion and artifact persistence are present, but no indexing is performed after successful conversion.

### SQLite Source-of-Truth State

- `database.py:19` keeps SQLite at `Database/research_notes.db`.
- `database.py:160` creates `reference_vault_documents`.
- `database.py:186` creates `reference_vault_chunks`.
- `database.py:198` includes `pinecone_vector_id` on Reference Vault chunks.
- `database.py:278-279` indexes `reference_vault_chunks(pinecone_vector_id)`.
- `database.py:546` defines `save_reference_vault_chunks_batch(...)`.
- `database.py:622` defines `get_reference_vault_chunks_by_vector_ids(...)`.
- `database.py:646` defines `update_reference_vault_chunk_vector_id(...)`.

Current reality: The SQLite shape needed for Phase 05 exists, but the active upload path does not populate `reference_vault_chunks`.

### Current Pinecone/RAG State

- `vector_store.py:47` hardcodes Pinecone embedding model `multilingual-e5-large`.
- `vector_store.py:266` defines `upsert_documents(...)` using direct Pinecone SDK calls.
- `vector_store.py:309` defaults missing `source_type` to `document`, not `reference_document`.
- `vector_store.py:352` upserts vectors into `namespace=user_id`.
- `vector_store.py:388` defines `ingest_note(...)`.
- `vector_store.py:411` indexes notes with `source_type='note'`.
- `vector_store.py:488` defines `retrieve_unified(...)` using direct Pinecone query logic.
- `vector_store.py:569` queries Pinecone in `namespace=user_id`.
- `vector_store.py:797` defines `enhanced_retrieve(...)`.
- `vector_store.py:882` broadens fallback by retrying without filters when filtered retrieval returns empty.

Current reality: Pinecone is active, but LlamaIndex is not used for chunking/indexing/retrieval orchestration. Legacy note indexing still exists in the module, and retrieval fallback can broaden beyond source filters.

### Current App Retrieval State

- `app.py:49` allows both `"document"` and `"reference_document"` in `REFERENCE_VAULT_SOURCE_TYPES`.
- `app.py:58` defines `_filter_reference_vault_docs(...)` as an app-side post-filter.
- `app.py:1014` calls `enhanced_retrieve(..., source_type="document")`.
- `app.py:1389` calls `retrieve_unified(..., source_type="document")`.
- `app.py:2275` calls `enhanced_retrieve(...)` for chat retrieval without a strict `source_type="reference_document"` argument.

Current reality: Some app paths filter after retrieval, but strict Reference Vault retrieval is not enforced at the retrieval service boundary. Several paths still use the legacy `document` source type.

### LlamaIndex Usage Search

Search across the current RAG-relevant source files did not find active imports or usage of:

- `llama_index`
- `VectorStoreIndex`
- `PineconeVectorStore`
- `SentenceSplitter`
- `StorageContext`
- LlamaIndex retrievers

The only LlamaIndex presence found in the targeted source set is dependency declarations in `requirements.txt`.

## Diff / Gap Analysis

| Prompt expectation | Current code reality | Gap |
| --- | --- | --- |
| Use LlamaIndex for chunks/nodes/indexing/retrieval | Current chunk/index/retrieve logic is direct Pinecone plus LangChain `Document` compatibility in `vector_store.py` | Need a LlamaIndex-backed Reference Vault indexing and retrieval service |
| Index Reference Vault documents only | Upload path is Reference Vault PDF-only, but legacy `ingest_note(...)` and source-type support remain in `vector_store.py` | v3 indexing entrypoints must reject notes/web and not expose fallback paths |
| Save chunks in SQLite | `reference_vault_chunks` table and helpers exist | Upload path does not create chunk rows after Docling conversion |
| Save Pinecone vector IDs in SQLite | `pinecone_vector_id` column and update helper exist | Current upsert IDs are generated internally and not returned/persisted for Reference Vault chunks |
| Required metadata includes `user_id`, `project_id`, `document_id`, `chunk_id`, `source_type=reference_document` | `upsert_documents(...)` defaults to `source_type=document`; metadata is flexible and legacy | Need mandatory metadata construction from SQLite chunk rows |
| Pinecone is vector index only | Current Pinecone metadata stores truncated content in `meta['content']` | For v3, SQLite should hold full content; Pinecone metadata should hold routing/verification metadata and only minimal optional snippet if needed |
| Strict retrieval against Reference Vault | Current retrieval can query with no source type and can retry without filters | Need a strict function that always filters `source_type=reference_document` and verifies returned vector IDs against SQLite |
| Reject metadata-only OpenAlex records | Reference Vault status exists, but retrieval does not check `reference_vault_documents.status` | Retrieval verification must require active/content-backed documents, not `metadata_only`, `processing`, `failed`, or `archived` |
| Do not retrieve Notes/Web | Current retrieval API supports `note` and `web_page`; app post-filter is not enough | v3 retrieval must reject any non-Reference Vault metadata before returning context |

## Detailed Implementation Plan

### Phase 05.1: Add a Narrow Reference Vault LlamaIndex Service

Create a new module, recommended name: `llamaindex_pinecone_rag.py`.

Reasoning: This avoids destabilizing `vector_store.py`, which still supports legacy app compatibility. A dedicated module also keeps Phase 05 small and auditable.

Initial public functions:

- `index_reference_vault_document(document_id: str, user_id: str, project_id: str | None = None) -> dict`
- `index_docling_result(docling_result) -> dict`
- `retrieve_reference_vault(query: str, user_id: str, project_id: str | None = None, k: int = 5) -> list`
- Optional compatibility adapter: `to_langchain_documents(nodes_or_results) -> list`

Implementation rules:

- Load the Reference Vault document from SQLite first.
- Require:
  - `status == "active"`
  - `docling_status == "succeeded"`
  - `docling_markdown_path` exists
  - `storage_path` exists for uploaded papers
- Reject:
  - `metadata_only`
  - `processing`
  - `failed`
  - `archived`
  - non-Reference Vault source types
- Do not accept notes, webpages, or arbitrary text documents as inputs.

### Phase 05.2: Build LlamaIndex Nodes From Docling Markdown

Use LlamaIndex for chunking/node creation.

Recommended approach:

- Read `docling_markdown_path` from SQLite.
- Build a LlamaIndex `Document` from Markdown text and trusted SQLite metadata.
- Use a LlamaIndex node parser such as `SentenceSplitter` or equivalent configured splitter.
- For each node:
  - assign or preserve a deterministic `chunk_id`
  - set `node.id_` to the `chunk_id` or a stable ID derived from `document_id + chunk_index + content_hash`
  - attach required metadata

Required metadata on every node/vector:

```json
{
  "user_id": "...",
  "project_id": "...",
  "document_id": "...",
  "chunk_id": "...",
  "source_type": "reference_document"
}
```

Recommended additional metadata:

```json
{
  "paper_name": "...",
  "author_display": "...",
  "publication_year": 2026,
  "doi": "...",
  "openalex_id": "...",
  "chunk_index": 0,
  "content_hash": "...",
  "section_title": "...",
  "docling_markdown_path": "..."
}
```

Do not rely on Pinecone metadata as source of truth. Treat all metadata copied into Pinecone as routing hints only.

### Phase 05.3: Persist SQLite Chunks Before Vector Upsert

Before writing to Pinecone:

1. Compute `content_hash` for each node text.
2. Create chunk records for `reference_vault_chunks`.
3. Persist via `database.save_reference_vault_chunks_batch(...)`.
4. Use each SQLite `chunk_id` as the LlamaIndex node/vector identity.

Chunk table requirements:

- `chunk_id`: primary identifier; should match node/vector ID or be recoverable from metadata.
- `document_id`: owning Reference Vault document.
- `user_id`: user scope.
- `project_id`: project scope.
- `chunk_index`: stable order from Markdown.
- `markdown_content`: original Markdown chunk when available.
- `content`: normalized retrievable text.
- `content_hash`: dedupe and idempotency support.
- `page_number`: optional, if derivable from Docling metadata.
- `section_title`: optional, if derivable.
- `pinecone_vector_id`: updated after Pinecone write.
- `embedding_model`: model name used by LlamaIndex embedding path.
- `source_type`: always `reference_document`.
- `metadata_json`: non-authoritative node metadata snapshot.

### Phase 05.4: Configure LlamaIndex + Pinecone Vector Store

Use the existing Pinecone environment variables:

- `PINECONE_API_KEY`
- `PINECONE_INDEX_NAME`
- `PINECONE_HOST`

Recommended implementation:

- Reuse existing Pinecone client/index setup where practical, but wrap it in LlamaIndex `PineconeVectorStore`.
- Use `StorageContext.from_defaults(vector_store=...)`.
- Use `VectorStoreIndex(nodes, storage_context=..., embed_model=...)` for indexing.
- Keep namespace behavior compatible with current `namespace=user_id`, unless the LlamaIndex Pinecone integration requires namespace passed through vector store construction or insert kwargs.

Embedding decision:

- Current code uses Pinecone Inference `multilingual-e5-large`.
- Phase 05 can either:
  - implement a small LlamaIndex embedding adapter around Pinecone Inference to preserve model behavior, or
  - choose an existing LlamaIndex embedding provider already configured by the project.
- Do not silently change embedding dimension/model without checking the Pinecone index dimension.

### Phase 05.5: Persist Pinecone Vector IDs

After LlamaIndex/Pinecone write:

- Ensure the vector ID for each node is known.
- Update `reference_vault_chunks.pinecone_vector_id` using `database.update_reference_vault_chunk_vector_id(...)`.
- If node ID is the vector ID, persist `chunk_id` as `pinecone_vector_id`.
- Mark document metadata `index_status="indexed"` only after all chunks have vector IDs.
- On partial failure, keep SQLite chunks but mark document metadata `index_status="failed"` or `partial_failed`; do not claim evidence-backed retrieval is available.

### Phase 05.6: Wire Only the Docling Success Path

Change the active upload path after `ingest_uploaded_pdf_with_docling(...)` succeeds.

Target location:

- `app.py:843` currently gets `docling_result`.
- `app.py:869` currently reports indexing pending.

Smallest later edit:

- Import the new indexing function.
- Call `index_docling_result(docling_result)` after successful Docling conversion.
- Display success only when indexing succeeds.
- Display a clear failed-indexing state if Docling succeeded but indexing failed.

Do not re-enable TXT/DOC/DOCX indexing. Keep Phase 05 scoped to Reference Vault PDFs converted by Docling.

### Phase 05.7: Add Strict Reference Vault Retrieval

Add retrieval through LlamaIndex, but keep strict verification after Pinecone returns candidates.

Retrieval rules:

- Always filter by:
  - `user_id`
  - `project_id` when available
  - `source_type="reference_document"`
- Never broaden fallback by dropping source filters.
- Never retrieve Notes/Web.
- Never return `metadata_only` documents.
- Never return chunks whose `pinecone_vector_id` is missing from SQLite.
- Never return chunks whose document is not `active`.
- Never return chunks whose document has no content artifact.

Verification algorithm:

1. Query Pinecone through LlamaIndex with strict metadata filters.
2. Extract vector IDs and/or `chunk_id` from returned nodes.
3. Load matching chunks with `database.get_reference_vault_chunks_by_vector_ids(...)` or a new `get_reference_vault_chunks_by_ids(...)`.
4. Load owning documents with user/project scope.
5. Reject anything where:
   - chunk missing in SQLite
   - user/project mismatch
   - `source_type != "reference_document"`
   - document missing
   - document `status != "active"`
   - document `docling_status != "succeeded"` for uploaded full-text papers
   - document is OpenAlex `metadata_only`
6. Return context built from SQLite chunk content and verified document metadata, not untrusted Pinecone metadata.

Compatibility:

- The app currently expects LangChain-like documents with `.page_content` and `.metadata`.
- For smallest safe integration, return a lightweight object or LangChain `Document` from verified SQLite rows until downstream code can consume a project-native context type.

### Phase 05.8: Make Legacy Retrieval Safe for v3 Paths

Keep legacy `retrieve_unified(...)` and `enhanced_retrieve(...)` for compatibility during transition, but avoid using them in Phase 05 v3 paths.

Later hardening in `vector_store.py`:

- Do not use `source_type="document"` in v3 Reference Vault calls.
- Remove or bypass the no-filter fallback at `vector_store.py:882` for v3 retrieval.
- Preserve function names where possible by adding a strict flag or separate wrapper:
  - `enhanced_retrieve(..., strict_reference_vault=True)`
  - or preferred: `retrieve_reference_vault(...)`.

## Files and Functions Likely To Change Later

Product code files:

- `llamaindex_pinecone_rag.py` new
  - `index_reference_vault_document(...)`
  - `index_docling_result(...)`
  - `retrieve_reference_vault(...)`
  - helper functions for node creation, Pinecone vector store, embedding, and SQLite verification
- `app.py`
  - upload success path around `ingest_uploaded_pdf_with_docling(...)`
  - chat/workbench retrieval call sites currently using `enhanced_retrieve(...)` or `retrieve_unified(...)`
- `database.py`
  - likely add `get_reference_vault_chunks_by_ids(...)`
  - likely add idempotent chunk delete/update helpers for re-indexing one document
  - likely add document index-status helper
- `vector_store.py`
  - optional compatibility reuse for Pinecone env/client setup
  - optional strict wrapper if app call sites cannot move directly to the new module

Test files:

- Add focused tests under `tests/`, likely:
  - `tests/test_phase05_llamaindex_pinecone_indexing.py`
  - or extend `tests/test_docling_ingestion.py` only if keeping the behavior tightly coupled to upload ingestion

Files not expected to change for Phase 05:

- `requirements.txt`, unless LlamaIndex imports fail because the currently listed packages are too broad or missing the embedding adapter package.
- `citation_generator.py`, because final citation rendering is a later citation-guard concern, though Phase 05 must preserve verified metadata needed by citations.
- `web_scraper.py`, unless active indexing references are being removed in a separate cleanup phase.

## Data Model and Metadata Requirements

### SQLite Document Requirements

`reference_vault_documents` must remain authoritative for:

- ownership: `user_id`, `project_id`
- status: `active`, `metadata_only`, `processing`, `failed`, `archived`
- source identity: `source_type`, `openalex_id`, `doi`
- citation identity: `paper_name`, `author_display`, `authors_json`, `publication_year`
- artifacts: `storage_path`, `docling_markdown_path`, `docling_status`
- indexing state: `metadata_json.index_status`

### SQLite Chunk Requirements

Every indexed chunk must have:

- `chunk_id`
- `document_id`
- `user_id`
- `project_id`
- `chunk_index`
- `content`
- `content_hash`
- `source_type="reference_document"`
- `embedding_model`
- `pinecone_vector_id`

Recommended invariant:

- `chunk_id == pinecone_vector_id == LlamaIndex node id`

This simplifies rehydration and verification. If the Pinecone integration generates a different vector ID, store both and always map Pinecone result IDs back to SQLite.

### Pinecone Vector Metadata Requirements

Required:

```json
{
  "user_id": "...",
  "project_id": "...",
  "document_id": "...",
  "chunk_id": "...",
  "source_type": "reference_document"
}
```

Optional routing/display metadata:

```json
{
  "paper_name": "...",
  "author_display": "...",
  "publication_year": 2026,
  "doi": "...",
  "openalex_id": "...",
  "chunk_index": 0,
  "section_title": "...",
  "content_hash": "..."
}
```

Avoid storing full authoritative content only in Pinecone. SQLite should provide the verified chunk content used for Workbench and citation context.

## Retrieval and Verification Rules

Strict v3 retrieval must:

- Use LlamaIndex retriever/query engine over Pinecone.
- Apply metadata filters before retrieval where supported.
- Use `source_type="reference_document"` only.
- Scope by `user_id`; scope by `project_id` where available.
- Verify every returned candidate against SQLite.
- Return content from SQLite rows, not unverified Pinecone metadata.
- Reject Notes and Web regardless of score.
- Reject OpenAlex metadata-only records unless a full-text upload exists and the document is `active`.
- Reject archived, failed, or processing documents.
- Reject chunks without a matching active document.
- Reject candidates missing `chunk_id` or `pinecone_vector_id`.
- Never retry by dropping Reference Vault filters.
- Return an empty result with an insufficient-evidence message when strict retrieval has no verified context.

Retrieval must not:

- Use legacy broad fallback from `enhanced_retrieve(...)` for v3 flows.
- Mix `source_type="document"` with `source_type="reference_document"` in the same v3 retrieval path.
- Treat Pinecone metadata as proof that a document exists in the Reference Vault.
- Allow LLM-generated citations from retrieved text alone.

## Test Plan

### Unit Tests

Add tests with Pinecone/LlamaIndex calls mocked:

1. `test_index_docling_result_creates_sqlite_chunks`
   - Given an active Docling document with Markdown.
   - When indexing runs.
   - Then `reference_vault_chunks` rows are created with required metadata.

2. `test_index_docling_result_upserts_required_pinecone_metadata`
   - Mock LlamaIndex/Pinecone vector store.
   - Assert every node/vector includes `user_id`, `project_id`, `document_id`, `chunk_id`, and `source_type="reference_document"`.

3. `test_index_docling_result_persists_vector_ids`
   - Assert `pinecone_vector_id` is written for every chunk after successful index.

4. `test_index_rejects_metadata_only_document`
   - Metadata-only OpenAlex record must not be indexed.

5. `test_index_rejects_failed_or_processing_docling_document`
   - `processing`, `failed`, and missing Markdown path must not be indexed.

6. `test_index_rejects_note_or_web_source_type`
   - Any non-Reference Vault source type raises or returns a structured rejection.

7. `test_retrieve_reference_vault_verifies_sqlite`
   - Pinecone returns candidates; only candidates present in SQLite with active documents are returned.

8. `test_retrieve_reference_vault_does_not_broaden_filters`
   - Empty strict retrieval returns empty; it must not call a second no-filter retrieval.

9. `test_retrieve_reference_vault_rejects_metadata_only`
   - Candidate from `metadata_only` document is discarded.

10. `test_retrieve_reference_vault_returns_sqlite_content`
    - If Pinecone metadata content differs from SQLite chunk content, returned context uses SQLite.

### Integration-Style Tests

Use temp SQLite and mocked vector store:

- Extend the Docling upload flow test currently asserting indexing is pending.
- New expected Phase 05 behavior should assert the upload path calls the indexing function after Docling success.
- Assert failed indexing does not mark the document as evidence-ready.

### Static / Smoke Checks

Run the smallest relevant checks after implementation:

- `python -m py_compile app.py database.py docling_ingestion.py llamaindex_pinecone_rag.py vector_store.py`
- Focused pytest:
  - `pytest tests/test_database_reference_vault_schema.py tests/test_docling_ingestion.py tests/test_phase05_llamaindex_pinecone_indexing.py`

Do not require live Pinecone in default tests. Live Pinecone should be covered by an explicitly marked smoke test with real credentials.

## Risks

- LlamaIndex/Pinecone API drift: package versions are unconstrained, so imports and vector store construction may differ across installations.
- Embedding compatibility: existing Pinecone index dimension likely matches `multilingual-e5-large`; changing embeddings could break upsert/query.
- Vector ID visibility: LlamaIndex abstractions may hide Pinecone IDs unless node IDs are explicitly controlled.
- Partial indexing: Docling can succeed while Pinecone fails. The UI and metadata must distinguish converted-but-not-indexed from evidence-ready.
- Duplicate indexing: repeated indexing must be idempotent or must cleanly replace old chunks/vectors for the same document.
- Legacy fallback leakage: app paths may still call `enhanced_retrieve(...)`, which can broaden filters.
- Source-type mismatch: current app still accepts `"document"` as Reference Vault-like, but Phase 05 vectors must use `"reference_document"`.
- Citation trust: Phase 05 provides verified chunks, but does not itself complete backend-rendered citation enforcement.

## Rollback and Compatibility Notes

- Add Phase 05 as an additive module first. This allows rollback by removing the upload-path call while keeping Docling ingestion intact.
- Keep legacy `vector_store.py` functions untouched until v3 retrieval call sites are migrated.
- If indexing fails in production, keep Docling document rows and artifacts but mark `index_status="failed"`; users can retry indexing without re-uploading the PDF.
- Use idempotent document re-indexing:
  - delete or supersede old vectors for the same `document_id`
  - replace SQLite chunks for that document in one controlled operation
  - only mark indexed after all chunks have vector IDs
- Do not delete legacy notes/web tables during Phase 05. The requirement is no active v3 indexing/retrieval, not destructive data removal.
- Keep app return types compatible with existing generator/reviewer code by returning LangChain-like documents or a small adapter until downstream code is refactored.

## Recommended Smallest Safe Edit Sequence

1. Add `llamaindex_pinecone_rag.py` with no app wiring.
2. Add unit tests with mocked LlamaIndex/Pinecone for node creation, metadata, SQLite chunk persistence, and strict retrieval verification.
3. Add idempotent SQLite helpers in `database.py` only if existing helpers are insufficient.
4. Wire `app.py` upload success path after `ingest_uploaded_pdf_with_docling(...)` to call `index_docling_result(...)`.
5. Update the upload success/failure message so "active" and "indexed" are distinct states.
6. Add strict retrieval wrapper and migrate one low-risk chat path to it.
7. Remove or bypass broad fallback for v3 retrieval paths.
8. Run py_compile and focused tests.

## Definition of Done for Phase 05

- A newly uploaded PDF is converted by Docling and indexed without using Notes/Web code paths.
- SQLite contains one active Reference Vault document and its chunk rows.
- Every chunk row has a `pinecone_vector_id`.
- Every Pinecone vector has required metadata with `source_type="reference_document"`.
- LlamaIndex is used for node/chunk creation and Pinecone indexing/retrieval orchestration.
- Retrieval returns only SQLite-verified Reference Vault chunks from active, content-backed documents.
- Empty strict retrieval does not broaden filters.
- Metadata-only OpenAlex records are not indexed or retrieved as evidence.
- Tests cover indexing metadata, SQLite persistence, strict retrieval verification, and fallback rejection.
