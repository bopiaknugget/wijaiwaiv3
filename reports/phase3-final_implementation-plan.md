# Phase 3 Final Implementation Plan

Date: 2026-05-02

Scope: Final implementation plan only. This report is the deliverable for Phase 3 planning; source code changes are not part of producing this file.

Goal: Implement the v3 UI/product scope with the smallest safe Streamlit changes: preserve the 3-panel workbench, make the left panel the Reference Vault, remove active Notes and Web Scraping surfaces, update outdated labels, keep SQLite as source of truth, and ensure user-visible citation and chat behavior is grounded only in Reference Vault records.

## 1. Product Decisions

- Keep the Streamlit UI as 3 panels:
  - Left: `Reference Vault`
  - Middle: `Workbench`
  - Right: `Chat Assistant`
- Treat the existing Streamlit sidebar as the left panel for Phase 3. Do not redesign navigation or introduce a new layout model.
- Keep SQLite. Do not migrate to PostgreSQL.
- Keep Pinecone as a vector index only. Do not treat Pinecone metadata as the trusted citation source.
- Reference Vault contains only:
  - Uploaded papers.
  - OpenAlex-imported paper metadata.
- Notes, research notes, web scraping, webpage ingestion, and saved webpage records are removed from active product scope.
- OpenAlex is metadata discovery and enrichment. Do not imply full text exists for every OpenAlex work.
- Citation display must use backend-verified SQLite metadata and final format:

```text
[author_name, paper_name]
```

## 2. Files To Change During Implementation

Primary UI implementation file:

- `app.py`

Implementation should stay UI-focused. Only expand beyond `app.py` if a true blocker prevents the UI from enforcing Reference Vault-only behavior.

Potential backend-touch files only if required by missing hooks:

- `database.py`, only to call existing Reference Vault/OpenAlex/citation helpers or add minimal read/list helper gaps. Do not remove legacy Notes/Web helpers.
- `vector_store.py` or retrieval wrapper code, only if Reference Vault-only retrieval cannot be enforced from `app.py`.
- OpenAlex client/import module, only if a backend worker has already provided it or Phase 3 explicitly includes the missing minimal hook.
- `citation_generator.py`, only if the existing citation UI cannot receive backend-rendered `[author_name, paper_name]` output through an existing function.

Files to preserve unchanged unless explicitly needed:

- `web_scraper.py`
- `requirements.txt`
- Existing SQLite database files
- Legacy database note/web compatibility functions

## 3. Left Panel: Reference Vault

Replace the current left-panel `Documents`, `Notes`, and `Web` tab set with one active Reference Vault surface.

Required visible sections:

- `Reference Vault` heading.
- Upload paper control.
- OpenAlex search/import control.
- Vault paper list.
- Processing status display.
- Metadata-only warning for OpenAlex-imported records without full text.

Upload paper behavior:

- Rename `Upload Documents` / `Process Documents` to paper-focused Reference Vault wording.
- Keep the existing upload flow functional while avoiding broad ingestion refactors.
- If the current upload path still writes legacy document metadata, the UI should still present it as Reference Vault only when the backing record can be treated as an uploaded paper.
- Narrow active upload UI to PDF-first paper uploads for the Docling target unless existing accepted file types are already validated as paper sources. Do not keep generic TXT/DOC/DOCX knowledge uploads as active v3 Reference Vault behavior.

OpenAlex UI behavior:

- Provide a left-panel search input and import action for OpenAlex metadata discovery.
- Search results should show paper name, author display, publication year, DOI/OpenAlex ID if available, source name, open-access/fulltext indicators, and import status.
- Importing an OpenAlex result creates or updates a Reference Vault metadata record in SQLite.
- OpenAlex-only records should be displayed as `metadata_only`.
- Metadata-only records must show a warning that the Vault has metadata but no uploaded/full-text paper content.
- Do not cite OpenAlex search results that have not been imported into Reference Vault.
- Do not assume `pdf_url` or `is_open_access` means the app has usable full text.
- If OpenAlex backend hooks are incomplete, show a disabled/import-unavailable state and document the backend blocker. Do not implement broad OpenAlex backend calls inside UI scope.

Notes/Web removal:

- Remove active note title/content input, save note button, note list, note preview, and note delete controls.
- Remove active webpage URL input, scrape button, scrape status flow, summarization/title generation, save/list/edit/delete webpage controls, and webpage chunk indexing.
- Remove `_show_web_edit_dialog()` if no active code path references it.
- Remove active `web_scraper` imports from `app.py` after Web UI paths are gone.
- Remove active `ingest_note` imports from `app.py` after Notes UI paths are gone.
- Do not keep Notes/Web as hidden-but-clickable tabs, secondary routes, or alternate UI flows.

## 4. Middle Panel: Workbench

Keep the middle panel as the writing and review surface.

Required changes:

- Rename visible `Research Workbench` wording to `Workbench` where it is the panel label.
- Replace `Knowledge Base` wording with `Reference Vault`.
- Keep text editor, edit/add/review/cite actions, citation suggestions, and unsupported-claim warnings.
- Source lists shown inside Workbench features must not label or display active `Note`, `research_note`, `web`, or `web_page` material.
- Workbench review and citation actions should retrieve only Reference Vault material.
- If Vault evidence is insufficient, show an unsupported-claim or insufficient-vault warning instead of implying the claim is supported by general AI knowledge.

Citation UI changes:

- Replace `Auto Citation - APA7` labels with Reference Vault citation wording.
- Remove active display assumptions for `[Author, Year]` and APA7 as the final product format.
- Show final citations as backend-rendered `[author_name, paper_name]`.
- Render citation text from SQLite Reference Vault metadata, not from LLM-generated prose.
- Display citation sources with trusted metadata: author display, paper name, document/status, and evidence excerpt where available.
- Do not let OpenThaiGPT or any LLM invent final citation text. Its role is limited to selecting allowed citation IDs.
- Metadata-only OpenAlex records are discoverable and importable into Reference Vault, but they are not full-text evidence by default. Citations should require uploaded/full-text Vault evidence unless a later phase explicitly authorizes metadata-only citation behavior.
- Final citation rendering belongs behind a backend-verified citation function that reads SQLite Reference Vault metadata and returns `[author_name, paper_name]`. If the existing citation path still emits APA-like output, Phase 3 should block or disable the citation action until the backend-rendered format is available rather than relabeling legacy output as v3 citations.

## 5. Right Panel: Chat Assistant

Keep the right panel as the Chat Assistant.

Required changes:

- Rename the panel heading from `Assistant` to `Chat Assistant`.
- Update chat source labels from generic docs/notes wording to Reference Vault wording.
- Chat Assistant retrieval must use only Reference Vault content.
- Filter out or ignore legacy Pinecone vectors with source types such as `note`, `research_note`, `web`, `web_page`, or webpage-derived metadata.
- If no reliable Vault content is retrieved, show an insufficient-vault fallback and avoid presenting a research-grounded answer.
- Small-talk and editor-control flows may remain, but they must not surface non-vault material as research evidence.

Fallback behavior:

- Use this ASCII-safe fallback text: `No sufficient supporting paper was found in the Reference Vault. Upload or import a relevant paper before using this as a research-grounded answer.`
- The fallback should be shown when Vault is empty, retrieval returns no trusted Reference Vault chunks, or retrieved chunks cannot be verified against SQLite Reference Vault records.

## 6. Smallest Safe Implementation Order

1. Relabel the three panels and active vocabulary.
2. Remove active Notes and Web UI blocks from `app.py`.
3. Clean now-unused Notes/Web imports and web edit dialog code.
4. Convert left panel into a single Reference Vault surface.
5. Wire the Vault list to Reference Vault records where available; keep legacy uploaded paper display only as a compatibility bridge if needed.
6. Add or expose OpenAlex search/import UI against existing backend hooks; if hooks are missing, add only the minimal UI placeholder/disabled state and document the backend blocker.
7. Update Workbench labels and source displays from `Knowledge Base`/APA wording to Reference Vault/citation wording.
8. Enforce Reference Vault-only source filtering for Workbench and Chat Assistant display paths.
9. Update citation result display to require backend-rendered `[author_name, paper_name]`.
10. Add insufficient-vault fallback messaging for Chat Assistant and Workbench citation/review paths.

## 7. Explicit Non-Goals

- Do not migrate away from SQLite.
- Do not delete existing SQLite data files.
- Do not delete `web_scraper.py`.
- Do not remove legacy note/web database helper functions.
- Do not remove scraping dependencies from `requirements.txt` in this phase.
- Do not redesign the app into a different layout or navigation system.
- Do not implement a full Docling ingestion refactor unless a separate phase authorizes it.
- Do not implement a full LlamaIndex/Pinecone rewrite unless a separate phase authorizes it.
- Do not hardcode API keys, OpenAlex base URLs, Pinecone hosts, or LLM secrets.
- Do not preserve active Notes/Web UI under different labels.

## 8. Verification

Syntax check:

```powershell
python -m py_compile app.py
```

Confirm removed active Notes/Web UI and routes:

```powershell
rg -n "st\.tabs|Notes|Web|save_note|load_all_notes|delete_note_by_id|save_web_page|load_all_web_pages|delete_web_page_by_id|scrape_url|summarize_content|generate_title|prepare_web_chunks|ingest_note|web_scrape|_show_web_edit_dialog" app.py
```

Confirm active web scraper imports are gone from `app.py`:

```powershell
rg -n "from web_scraper|import web_scraper|scrape_url|summarize_content|generate_title|prepare_web_chunks" app.py
```

Confirm v3 labels are present and outdated labels are removed or limited to comments/legacy helpers:

```powershell
rg -n "Reference Vault|Workbench|Chat Assistant|Knowledge Base|Auto Citation|APA7|Documents" app.py
```

Confirm retrieval/source displays do not expose legacy source types:

```powershell
rg -n "source_type|note|research_note|web_page|web" app.py
```

Confirm Reference Vault/OpenAlex/citation SQLite helpers remain available:

```powershell
rg -n "reference_vault_documents|list_reference_vault_documents|save_reference_vault_document|openalex_search_cache|cache_openalex_search|save_citation_candidate|save_citation_log" database.py
```

Manual UI checks:

- Left panel heading is `Reference Vault`.
- Middle panel heading is `Workbench`.
- Right panel heading is `Chat Assistant`.
- No user can create/list/delete Notes from active UI.
- No user can scrape, save, edit, list, or delete webpages from active UI.
- OpenAlex search/import UI clearly presents metadata discovery and metadata-only status.
- Citation output uses `[author_name, paper_name]`.
- Chat Assistant shows an insufficient-vault fallback when the Vault has no trusted supporting content.

## 9. Rollback Risks

- Removing Notes/Web blocks before cleaning imports can leave startup-breaking references.
- Cleaning imports before all UI references are removed can produce `NameError`.
- If retrieval remains broad, legacy note/web vectors may still appear even after the UI no longer creates them.
- If citation labels are changed without citation rendering changes, the UI may claim v3 citations while still producing APA-style output.
- If OpenAlex search results are shown without import/status distinction, users may assume non-imported metadata is trusted Vault evidence.
- If metadata-only records are treated like full-text papers, Chat Assistant and Workbench may overstate evidence quality.
- If legacy database helpers are removed, compatibility and existing data access may break.
- If the implementation expands into Docling/LlamaIndex rewrites, Phase 3 rollback becomes coupled to unrelated architecture work.

## 10. Implementation Defaults

- If OpenAlex backend hooks are incomplete, Phase 3 UI should show a disabled/import-unavailable state and document the backend blocker in the UI or implementation notes. Do not implement broad OpenAlex backend calls inside UI scope.
- Narrow active upload UI to PDF-first paper uploads for the Docling target unless existing accepted file types are already validated as paper sources. Do not keep generic TXT/DOC/DOCX knowledge uploads as active v3 Reference Vault behavior.
- Metadata-only OpenAlex records are discoverable and importable into Reference Vault, but they are not full-text evidence by default. Citations should require uploaded/full-text Vault evidence unless a later phase explicitly authorizes metadata-only citation behavior.
- Final citation rendering belongs behind a backend-verified citation function that reads SQLite Reference Vault metadata and returns `[author_name, paper_name]`. If the existing citation path still emits APA-like output, Phase 3 should block or disable the citation action until the backend-rendered format is available rather than relabeling legacy output as v3 citations.
