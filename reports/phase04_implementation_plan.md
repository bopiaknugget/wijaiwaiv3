# Phase 04 Implementation Plan: Docling Ingestion Pipeline

Generated: 2026-05-03

Source review:
- `implementation_prompts/phase_04_docling_ingestion_pipeline.md`
- Current code inspection by `docling_ingestion_worker`
- Phase 04 validation notes from Wijaiwai project advisor

---

## 1. Scope and Constraints

Phase 04 introduces Docling-based PDF ingestion for Reference Vault uploads.

Uploaded PDFs must be converted to Markdown before downstream metadata extraction, chunking, and vector indexing.

This phase focuses only on:

```text
Reference Vault PDF upload
↓
Artifact storage
↓
Docling PDF-to-Markdown conversion
↓
SQLite status update
↓
Markdown artifact prepared for Phase 05
```

Phase 04 must not implement the full LlamaIndex + Pinecone RAG pipeline.

---

## 2. Non-Negotiable Constraints

- Keep SQLite as the source of truth.
- Do not migrate to PostgreSQL.
- Keep OpenAlex integration as metadata discovery and enrichment.
- Keep Reference Vault as the only active document source.
- Do not preserve Notes or Web Scraping in active ingestion scope.
- Reference Vault should contain only:
  - uploaded papers
  - OpenAlex-imported paper metadata
- Use Docling for uploaded PDF-to-Markdown conversion.
- Prepare Docling Markdown output for LlamaIndex + Pinecone RAG.
- Do not implement full LlamaIndex chunking/indexing in Phase 04.
- Do not upsert vectors to Pinecone in Phase 04.
- Pinecone remains a vector index only.
- SQLite remains the source of truth for document metadata.
- Do not fabricate citations.
- Backend citation rendering must use verified Reference Vault metadata.
- Preserve existing function names where possible.
- Make the smallest safe change.
- Do not redesign the UI.
- Do not rewrite the full upload pipeline.
- Do not remove legacy functions unless they are proven unused and safe to remove.

---

## 3. Phase 04 Boundary Clarification

This phase must only convert uploaded Reference Vault PDFs into Docling Markdown artifacts and update SQLite document status.

### Allowed in Phase 04

- Add `docling` dependency.
- Add a focused Docling ingestion module.
- Store uploaded PDF artifacts.
- Convert uploaded PDF to Markdown using Docling.
- Write `docling.md`.
- Write `docling_meta.json`.
- Insert or update Reference Vault document status in SQLite.
- Mark document indexing as pending for Phase 05.
- Preserve legacy `load_document()` and PyPDF path for compatibility.
- Route active Reference Vault PDF upload through Docling instead of PyPDFLoader.

### Not Allowed in Phase 04

Do not implement:

- LlamaIndex chunking.
- LlamaIndex embeddings.
- Pinecone upsert rewrite.
- Full vector indexing.
- Workbench citation service.
- OpenAlex metadata search.
- OpenAlex import flow.
- Chat assistant RAG rewrite.
- Citation candidate generation.
- Citation rendering.
- UI redesign.
- Large `app.py` refactor.
- Full removal of legacy document loader code.

### Important Rule

After Docling succeeds, the document should be ready for Phase 05 indexing, but not indexed yet.

Use metadata such as:

```json
{
  "index_status": "pending",
  "ingestion_phase": "docling_completed"
}
```

inside `metadata_json` or `extraction_metadata_json`.

---

## 4. Current Code Findings

Current code inspection found:

- `app.py` already narrows the active upload UI to a single PDF upload.
- Upload processing still writes a temporary PDF and calls the legacy `load_document(tmp_path)` path.
- `document_loader.py` still uses `PyPDFLoader` for PDF loading instead of Docling.
- Active upload still saves document metadata through the legacy `save_document_metadata()` path.
- `database.py` already has partial v3 Reference Vault support, including:
  - `reference_vault_documents`
  - `reference_vault_chunks`
  - `storage_path`
  - `docling_markdown_path`
  - `docling_status`
- Existing Reference Vault helpers exist, such as:
  - `save_reference_vault_document()`
  - document status helpers
  - chunk helpers
- These helpers are not yet used by active upload.
- `vector_store.py` currently generates Pinecone vector IDs internally and returns only a count.
- SQLite cannot yet reliably store each chunk's Pinecone vector ID.
- `requirements.txt` includes PDF, LangChain, and Pinecone dependencies.
- `requirements.txt` does not include Docling.
- `requirements.txt` does not include LlamaIndex.

---

## 5. Proposed Smallest-Safe Implementation Plan

### Step 1: Add Docling dependency

Update:

```text
requirements.txt
```

Add:

```text
docling
```

Do not add LlamaIndex dependencies in Phase 04 unless a test boundary absolutely requires it.

LlamaIndex dependencies belong to Phase 05.

---

### Step 2: Add focused Docling ingestion module

Create:

```text
docling_ingestion.py
```

This module should be responsible for:

- receiving uploaded PDF path or bytes
- creating Reference Vault artifact directories
- storing `original.pdf`
- running Docling conversion
- writing `docling.md`
- writing `docling_meta.json`
- returning conversion results
- returning structured error metadata on failure

This module should not:

- call Pinecone
- call LlamaIndex
- call OpenThaiGPT
- call OpenAlex
- generate citations
- create embeddings
- update UI directly

---

### Step 3: Artifact storage structure

Store Reference Vault artifacts under:

```text
storage/reference_vault/{user_id}/{project_id}/{document_id}/original.pdf
storage/reference_vault/{user_id}/{project_id}/{document_id}/docling.md
storage/reference_vault/{user_id}/{project_id}/{document_id}/docling_meta.json
```

Rules:

- Use safe path construction.
- Do not use raw user input directly in filesystem paths.
- Ensure directories are created idempotently.
- Avoid overwriting another document's artifact folder.
- Keep paths relative when practical.
- Store absolute paths only if existing code convention requires them.

---

### Step 4: Insert Reference Vault row before conversion

Before running Docling, insert a Reference Vault document row.

Initial status:

```text
status="processing"
docling_status="processing"
source_type="reference_document"
```

Required fields should include:

```text
document_id
user_id
project_id
filename
file_type
source_type
status
docling_status
storage_path
metadata_json
created_at
updated_at
```

If the current app does not have `project_id`, use:

```text
get_default_project_id(user_id)
```

or the compatible default project helper created in Phase 02.

---

### Step 5: Run Docling conversion

Docling should convert the uploaded PDF to Markdown.

Expected output:

```text
markdown_text
conversion_metadata
```

Write Markdown to:

```text
docling.md
```

Write metadata to:

```text
docling_meta.json
```

Metadata should include, where available:

```text
converter_name
converter_version
source_filename
document_id
user_id
project_id
created_at
page_count
ocr_used
ocr_backend
warnings
errors
artifact_paths
```

If Docling does not provide a specific field, do not fabricate it.

---

### Step 6: Success status handling

On Docling success, update the Reference Vault document row:

```text
status="active"
docling_status="succeeded"
storage_path=<original.pdf path>
docling_markdown_path=<docling.md path>
extraction_metadata_json=<conversion metadata and docling_meta path>
```

Also add pending indexing metadata:

```json
{
  "index_status": "pending",
  "ingestion_phase": "docling_completed"
}
```

Do not create embeddings in Phase 04.

Do not upsert to Pinecone in Phase 04.

Do not create final citation candidates in Phase 04.

---

### Step 7: Failure status handling

On Docling failure:

Update the Reference Vault document row:

```text
status="failed"
docling_status="failed"
extraction_metadata_json=<error metadata>
```

Error metadata should include:

```text
error_type
error_message
failed_at
source_filename
document_id
user_id
project_id
```

Failure rules:

- Do not create chunks.
- Do not call Pinecone.
- Do not call LlamaIndex.
- Do not mark the document as active.
- Do not silently swallow the error.
- Show a clear user-facing message in the app.
- Keep technical error details in logs/metadata, not exposed directly if sensitive.

---

### Step 8: Preserve legacy document loader

Preserve:

```text
document_loader.py
load_document()
PyPDFLoader path
```

for legacy compatibility or existing tests.

However, active Reference Vault PDF upload should stop using:

```text
load_document(tmp_path)
```

for new PDF uploads.

Instead, active upload should use:

```text
docling_ingestion.py
```

The legacy path can remain for:

- old tests
- CLI compatibility
- temporary fallback
- non-Reference Vault flows if still present

Do not remove it in Phase 04.

---

### Step 9: Minimal app.py integration

Update `app.py` only where necessary to route active Reference Vault PDF upload through the Docling ingestion path.

Allowed `app.py` changes:

- replace active PDF upload processing from `load_document(tmp_path)` to Docling ingestion
- insert Reference Vault document row before conversion
- update Reference Vault document status after conversion
- show success/failure message
- avoid Notes/Web ingestion path
- preserve 3-panel layout
- preserve current app startup

Not allowed:

- UI redesign
- major layout rewrite
- chat assistant rewrite
- Workbench citation rewrite
- OpenAlex UI implementation
- LlamaIndex indexing implementation
- Pinecone indexing rewrite

---

### Step 10: database.py helper adjustment

Use existing Phase 02 Reference Vault helpers where possible.

Only add or adjust a small helper if required.

Possible helper:

```python
update_reference_vault_document_docling_status(
    document_id,
    status,
    docling_status,
    storage_path=None,
    docling_markdown_path=None,
    extraction_metadata_json=None,
    metadata_json=None,
    user_id=None,
    project_id=None,
)
```

Rules:

- Do not broadly refactor `database.py`.
- Do not delete legacy tables.
- Do not delete Notes/Web functions.
- Do not introduce PostgreSQL.
- Do not change existing public function signatures unless unavoidable.
- Prefer adding a helper over changing many call sites.

---

## 6. Exact Files or Modules Likely To Change

### Required

```text
requirements.txt
docling_ingestion.py
app.py
```

### Allowed if needed

```text
database.py
tests/test_docling_ingestion.py
tests/test_reference_vault_docling_status.py
```

### Avoid in Phase 04

```text
vector_store.py
citation_generator.py
reviewer.py
generator.py
openalex_client.py
document_loader.py
web_scraper.py
```

`document_loader.py` may be inspected, but should not need major changes.

---

## 7. Data Flow and Status Handling

### Successful Flow

```text
User uploads PDF
↓
App resolves user_id
↓
App resolves or creates project_id
↓
App creates document_id
↓
App creates storage folder
↓
App saves original.pdf
↓
App inserts Reference Vault row:
    status="processing"
    docling_status="processing"
    source_type="reference_document"
↓
Docling converts PDF to Markdown
↓
App writes docling.md
↓
App writes docling_meta.json
↓
App updates Reference Vault row:
    status="active"
    docling_status="succeeded"
    storage_path=<original.pdf path>
    docling_markdown_path=<docling.md path>
    extraction_metadata_json=<metadata>
    index_status="pending"
↓
Phase 05 will later chunk/index Markdown with LlamaIndex + Pinecone
```

### Failure Flow

```text
User uploads PDF
↓
App resolves user_id and project_id
↓
App creates document_id
↓
App saves original.pdf if possible
↓
App inserts Reference Vault row:
    status="processing"
    docling_status="processing"
↓
Docling conversion fails
↓
App updates Reference Vault row:
    status="failed"
    docling_status="failed"
    extraction_metadata_json=<error metadata>
↓
No chunks are saved
↓
No Pinecone vectors are upserted
↓
User sees clear failure message
```

---

## 8. Scanned PDF and OCR Handling

Phase 04 should support scanned PDFs only when the Docling OCR runtime is properly configured.

Do not claim scanned PDF support is fully verified unless tested in the target environment.

Recommended wording:

```text
Scanned PDF support depends on the configured Docling OCR backend.
```

or in Thai UI/log:

```text
การรองรับ PDF แบบสแกนขึ้นอยู่กับ OCR backend ที่ตั้งค่าไว้ใน Docling runtime
```

Implementation rules:

- Do not hardcode OCR backend assumptions.
- Do not claim OCR success unless conversion metadata supports it.
- Store OCR-related metadata only when available.
- If scanned PDF conversion fails, mark the document as failed and provide a clear message.
- Do not silently fall back to empty text.
- Do not index empty Markdown.

---

## 9. Dependencies and Configuration Needed

Add:

```text
docling
```

to:

```text
requirements.txt
```

Do not add LlamaIndex dependencies in Phase 04 unless required by an isolated test boundary.

Keep existing environment variable boundaries:

```text
PINECONE_API_KEY
PINECONE_INDEX_NAME
PINECONE_HOST
OPEN_ALEX_API_KEY
OPEN_ALEX_BASE_URL
OPENTHAI_API_KEY
```

Do not hardcode:

- API keys
- OpenAlex base URL
- Pinecone host
- LLM endpoint
- user IDs
- project IDs
- absolute developer-machine paths

---

## 10. Relationship to Phase 05

Phase 04 prepares the Markdown artifact.

Phase 05 should handle:

```text
Docling Markdown
↓
LlamaIndex document/node creation
↓
Chunking
↓
Embedding
↓
Pinecone upsert
↓
Store chunk/vector mappings in SQLite
↓
Reference Vault retrieval
```

Phase 04 should not attempt to solve Pinecone vector ID traceability.

Known issue for Phase 05:

```text
vector_store.py currently generates Pinecone vector IDs internally and returns only a count.
```

This blocks full SQLite chunk-to-vector traceability until the vector layer is adjusted.

Do not solve this in Phase 04.

---

## 11. Testing Strategy

### Unit Tests

Tests should mock the Docling conversion boundary where possible.

Do not require real OCR runtime for unit tests.

Recommended tests:

```text
tests/test_docling_ingestion.py
tests/test_reference_vault_docling_status.py
```

Test cases:

1. Successful artifact creation:
   - creates `original.pdf`
   - creates `docling.md`
   - creates `docling_meta.json`

2. Successful status update:
   - document becomes `status="active"`
   - document becomes `docling_status="succeeded"`
   - `docling_markdown_path` is saved
   - `extraction_metadata_json` includes `index_status="pending"`

3. Failure status update:
   - document becomes `status="failed"`
   - document becomes `docling_status="failed"`
   - error metadata is saved

4. Failure safety:
   - no chunks are saved
   - no Pinecone upsert is called
   - no indexing is triggered

5. Existing schema test still passes:
   - `tests/test_database_reference_vault_schema.py`

---

## 12. Verification Checklist

### Static Compile

Run:

```powershell
python -m py_compile app.py database.py document_loader.py vector_store.py docling_ingestion.py
```

If using Windows venv:

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py database.py document_loader.py vector_store.py docling_ingestion.py
```

### Existing Schema Test

Run if `pytest` is available:

```powershell
python -m pytest tests/test_database_reference_vault_schema.py
```

or:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_database_reference_vault_schema.py
```

### New Tests

Run if added:

```powershell
python -m pytest tests/test_docling_ingestion.py
python -m pytest tests/test_reference_vault_docling_status.py
```

or:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_docling_ingestion.py
.\.venv\Scripts\python.exe -m pytest tests/test_reference_vault_docling_status.py
```

---

## 13. Manual Smoke Test

Manual test steps:

1. Start Streamlit.

   ```powershell
   streamlit run app.py
   ```

2. Upload a normal text-based PDF to Reference Vault.

3. Confirm:

   ```text
   storage/reference_vault/{user_id}/{project_id}/{document_id}/original.pdf
   storage/reference_vault/{user_id}/{project_id}/{document_id}/docling.md
   storage/reference_vault/{user_id}/{project_id}/{document_id}/docling_meta.json
   ```

   are created.

4. Confirm SQLite `reference_vault_documents` row becomes:

   ```text
   status="active"
   docling_status="succeeded"
   ```

5. Confirm `docling_markdown_path` is saved.

6. Confirm `extraction_metadata_json` includes Docling metadata and index pending metadata.

7. Confirm active upload no longer uses `PyPDFLoader`.

8. Upload a bad/corrupt PDF.

9. Confirm SQLite row becomes:

   ```text
   status="failed"
   docling_status="failed"
   ```

10. Confirm no chunks are created.

11. Confirm no Pinecone upsert occurs.

12. Optional: test scanned PDF only if the local Docling OCR runtime is configured.

---

## 14. Risks and Blockers

### Docling OCR Runtime Risk

Docling may require additional OCR/runtime dependencies for scanned PDFs.

Do not treat scanned PDF support as verified until tested on the target Windows environment.

### Metadata Path Risk

`docling_meta.json` has no dedicated database path column.

Smallest safe path:

```text
store docling_meta.json path inside extraction_metadata_json
```

A dedicated column can be added later if needed.

### RAG Boundary Risk

Active RAG still uses legacy LangChain and `parent_chunks`.

Full LlamaIndex orchestration belongs to Phase 05.

### Pinecone Traceability Risk

`vector_store.py` currently generates Pinecone vector IDs internally and returns only a count.

This blocks complete SQLite chunk-to-vector traceability until Phase 05 adjusts the vector/indexing layer.

### Legacy Notes/Web Risk

Current code still contains legacy Notes/Web helpers.

Phase 04 should avoid expanding or depending on those paths.

### Temporary File Cleanup Risk

Temporary uploaded PDFs must be removed or safely managed even if conversion fails.

Prefer writing directly to the Reference Vault artifact folder instead of writing duplicate temporary files.

### app.py Size Risk

`app.py` is already large.

Keep Phase 04 changes surgical.

Do not use Phase 04 as an opportunity to refactor UI architecture.

---

## 15. Files Inspected By Reviewer

- `implementation_prompts/phase_04_docling_ingestion_pipeline.md`
- `_context_packs/docling_ingestion_rules.md`
- `_context_packs/llamaindex_pinecone_rag_rules.md`
- `_context_packs/data_model_sqlite.md`
- `app.py`
- `document_loader.py`
- `database.py`
- `vector_store.py`
- `requirements.txt`
- `tests/test_database_reference_vault_schema.py`

No implementation code was modified while preparing this report.

---

## 16. Final Implementation Instruction For Codex

Use this implementation boundary when executing Phase 04:

```text
Implement Phase 04 only: Docling Ingestion Pipeline.

Use docling_ingestion_worker.

Read first:
- AGENTS.md
- docs/codex_reviews/v3_sqlite_openalex_docling_preflight_review.md
- docs/implementation_plans/phase_04_docling_ingestion_pipeline_plan.md
- _context_packs/docling_ingestion_rules.md
- _context_packs/reference_vault_rules.md
- _context_packs/data_model_sqlite.md
- _context_packs/architecture.md
- implementation_prompts/phase_04_docling_ingestion_pipeline.md

Scope:
- Add Docling dependency.
- Add docling_ingestion.py.
- Route active Reference Vault PDF upload through Docling.
- Store original.pdf, docling.md, and docling_meta.json.
- Update SQLite Reference Vault document status.
- Mark index_status as pending.
- Preserve load_document() for legacy compatibility.

Do not:
- Implement LlamaIndex chunking.
- Implement embeddings.
- Upsert vectors to Pinecone.
- Rewrite vector_store.py.
- Implement OpenAlex.
- Implement Workbench citation.
- Redesign UI.
- Delete Notes/Web legacy database functions.
- Remove PyPDFLoader legacy path.

Allowed files:
- requirements.txt
- docling_ingestion.py
- app.py
- database.py only if a small status helper is needed
- focused tests

Verification:
- python -m py_compile app.py database.py document_loader.py vector_store.py docling_ingestion.py
- pytest tests/test_database_reference_vault_schema.py if pytest is available
- pytest new Docling tests if added
- Manual upload smoke test if practical

Final response:
1. Summary of Phase 04 changes
2. Files changed
3. New Docling flow
4. SQLite status behavior
5. What was intentionally not changed
6. Checks run
7. Risks/TODO for Phase 05
```

---

## 17. Approval Status

Status:

```text
Approved with boundary clarification.
```

Phase 04 can proceed after confirming:

```text
Phase 04 = Docling conversion + artifacts + SQLite status only.
Phase 05 = LlamaIndex chunking/indexing + Pinecone traceability.
```
