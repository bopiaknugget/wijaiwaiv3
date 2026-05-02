# Phase 3 Task 1 UI Review Only

Date: 2026-05-02

Scope: Review only. No code changes were made. This review covers the smallest safe UI and active-flow changes needed to remove Notes and Web Scraping from active product scope while preserving the 3-panel Wijaiwai layout.

## Context Read

- `AGENTS.md`
- `docs/codex_reviews/v3_sqlite_openalex_docling_preflight_review.md`
- `_context_packs/ui_rules.md`
- `_context_packs/reference_vault_rules.md`
- `implementation_prompts/phase_03_disable_notes_and_web_scope.md`

Notes:

- The requested `implementation_prompts/phase_03_disable_notes_and_web_active_scope.md` file is not present. The closest matching prompt is `implementation_prompts/phase_03_disable_notes_and_web_scope.md`.
- No `docs/codex_reviews/phase_02 review/result` path was found under `docs/codex_reviews`.

## 1. Current Notes/Web UI Locations

- `app.py:2-3` still describes the product as "RAG with Text Notes" and names the layout as `Sidebar (Docs + Notes) | Center (Research Workbench) | Right (Assistant chat)`.
- `app.py:833-840` renders the left sidebar heading and creates three tabs: `Documents`, `Notes`, and `Web`.
- `app.py:843-1009` is the current Documents tab. This is the closest existing UI surface to rename/reframe as `Reference Vault`.
- `app.py:1011-1081` is the active Notes tab. It exposes note title/content inputs, save, list, preview, and delete controls.
- `app.py:1083-1205` is the active Web tab. It exposes URL input, scrape action, saved web-page list, edit, delete, and calls the web edit dialog.
- `app.py:318-370` defines `_show_web_edit_dialog()`, which is only needed by the active Web tab.
- `app.py:1211-1215` preserves the center panel as the Workbench column.
- `app.py:2193-2203` preserves the right panel but labels it `Assistant`, not `Chat Assistant`.

## 2. Active Call Paths To Disable

Notes:

- `app.py:1031-1041` saves notes with `database.save_note()` and immediately indexes them into Pinecone with `ingest_note()`.
- `app.py:1051-1081` loads saved notes through `database.load_all_notes()`.
- `app.py:1061-1069` deletes notes through `database.delete_note_by_id()` and removes vectors through `delete_by_metadata("note_id", ...)`.
- `vector_store.py:388-426` defines `ingest_note()` and stores note chunks with `source_type="note"`.
- `database.py:777-819` contains legacy note persistence helpers. Keep these for compatibility unless a later cleanup phase explicitly removes legacy data support.

Web:

- `app.py:1101-1107` calls `scrape_url()` from the Web tab.
- `app.py:1115-1126` calls `summarize_content()` and `generate_title()`.
- `app.py:1136-1148` stores web metadata through `database.save_web_page()` and builds chunks through `prepare_web_chunks()`.
- `app.py:1150-1155` indexes web chunks into Pinecone through `ingest_documents()`.
- `app.py:1157-1167` updates the web-page row and records token usage as `web_scrape`.
- `app.py:1176-1205` loads, edits, deletes, and opens the edit dialog for saved web pages.
- `web_scraper.py:63`, `web_scraper.py:214`, `web_scraper.py:268`, and `web_scraper.py:326-328` define the active scrape, summarize, title, and web chunking functions.
- `database.py:959-1050` contains legacy web-page persistence helpers. Keep these for compatibility unless a later cleanup phase explicitly removes legacy data support.

Retrieval/display surfaces to watch:

- `vector_store.py:510` and `vector_store.py:828` still document allowed retrieval `source_type` values including `note` and `web_page`.
- `app.py:1479-1481`, `app.py:2064-2066`, `app.py:2148-2154`, and `app.py:2225-2229` still render retrieved sources as `Note` when metadata says `note` or `research_note`.
- `app.py:1584-1589`, `app.py:2498-2503`, `app.py:2570-2575`, and `app.py:2653-2659` call `enhanced_retrieve()` without a `source_type` filter, so legacy note/web vectors can still appear if they already exist in Pinecone.

## 3. Imports That Can/Cannot Be Safely Removed

Safely removable from active `app.py` after the Web tab and dialog are hidden/removed:

- `app.py:47`: `from web_scraper import scrape_url, summarize_content, generate_title, prepare_web_chunks`

Safely removable from active `app.py` after the Notes tab is hidden/removed:

- `app.py:53`: `ingest_note` from the `vector_store` import list

Should remain for this minimal phase:

- `delete_document` and `delete_by_metadata` from `app.py:56-57` because `delete_document` is still used by document deletion, and `delete_by_metadata` is used by the existing Notes delete path until that path is removed.
- `database` import at `app.py:26` because document/workbench/session flows still use it.
- `web_scraper.py` file itself should not be deleted in this phase. Remove only the active import/routing from `app.py`.
- `database.py` legacy note/web functions should not be deleted in this phase. The phase prompt says to keep legacy functions for compatibility.
- `vector_store.ingest_note()` can remain as a legacy helper if no active UI/routing imports or calls it.

## 4. Files That Should Be Changed

Smallest safe implementation target:

- `app.py`
  - Rename the left sidebar surface from generic source/docs wording to `Reference Vault`.
  - Replace the three-tab sidebar with a single active Reference Vault area or a one-tab-only layout.
  - Hide/remove the Notes tab block at `app.py:1011-1081`.
  - Hide/remove the Web tab block at `app.py:1083-1205`.
  - Remove `_show_web_edit_dialog()` at `app.py:318-370` if no remaining code references it.
  - Remove the active `web_scraper` import at `app.py:47`.
  - Remove the active `ingest_note` import at `app.py:53`.
  - Relabel right panel `Assistant` to `Chat Assistant` at `app.py:2202`.
  - Prefer `Reference Vault` wording for retrieved source labels instead of `Doc`/`Note` where touched.
  - Add explicit `source_type="document"` or the current Reference Vault equivalent to broad active retrieval calls where practical for this phase.

Optional follow-up cleanup after the minimal UI/routing pass:

- `vector_store.py`
  - Update docstrings/comments that advertise `note` and `web_page` as active retrieval filters.
  - Keep `ingest_note()` only as legacy compatibility or move it out of active import paths.

## 5. Files That Must Not Be Changed In This Phase

- `database.py`: Do not delete note/web tables or helper functions in the UI phase. Existing legacy data and compatibility paths may depend on them.
- `web_scraper.py`: Do not delete the module in this phase. Removing the active import and UI route is enough.
- `requirements.txt`: Do not remove web scraping dependencies in this phase unless a later dependency cleanup phase verifies no legacy/import/test usage remains.
- `citation_generator.py`: Citation format and trust changes belong to the Workbench citation phase, not this UI scope.
- `document_loader.py`: Docling ingestion changes belong to the ingestion phase, not this UI scope.
- `generator.py` and `reviewer.py`: OpenThaiGPT provider behavior should remain untouched for this UI-only scope.
- Existing SQLite database files: Do not migrate, delete, or rewrite user data.

## 6. Minimal Implementation Plan

1. In `app.py`, change the sidebar label/header to `Reference Vault` while preserving its left-panel location.
2. Replace `st.tabs(["Documents", "Notes", "Web"])` with only the document/upload management surface, renamed to Reference Vault.
3. Remove or guard out the Notes block so `database.save_note()`, `database.load_all_notes()`, `database.delete_note_by_id()`, `ingest_note()`, and note vector deletion are no longer reachable from active UI.
4. Remove or guard out the Web block so scraping, summarization, title generation, web-page persistence, web chunking, web indexing, token usage `web_scrape`, and web edit/delete UI are no longer reachable.
5. Remove `_show_web_edit_dialog()` if the Web block is removed and no references remain.
6. Remove the `web_scraper` import and `ingest_note` import from `app.py`.
7. Relabel the right panel from `Assistant` to `Chat Assistant`.
8. Add active retrieval filters in broad Workbench/Chat Assistant retrieval calls where this can be done without changing backend contracts. At minimum, prevent legacy note/web vectors from surfacing in chat and workbench source displays.
9. Run a minimal syntax check such as `python -m py_compile app.py` after implementation.

## 7. Risks

- Existing Pinecone namespaces may already contain `note` and `web_page` vectors. Hiding UI stops new ingestion but does not by itself prevent old vectors from being retrieved.
- Broad `enhanced_retrieve()` calls without `source_type` filters can still surface legacy note/web content until retrieval is constrained.
- Removing the Web tab before removing `web_scraper` imports would still leave web scraping in the active app import path.
- Removing legacy database functions or tables now would be higher risk than needed and could break compatibility or existing tests.
- The current upload UI still says `Documents` and accepts `PDF/TXT/DOCX/DOC`; this is not fully aligned with Reference Vault paper-only language, but the smallest safe Phase 3 change can relabel and scope active UI without completing Docling/OpenAlex phases.
- Some UI labels still say `Research Workbench`, `Assistant`, `Doc`, and `Note`; label cleanup should be tightly scoped to visible active surfaces touched by this phase.
