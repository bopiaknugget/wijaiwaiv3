You are working on the Wijaiwai / Research Workbench codebase.

Global v3 decisions:
- Keep SQLite. Do not migrate to PostgreSQL.
- Keep OpenAlex using `.env` variables `OPEN_ALEX_API_KEY` and `OPEN_ALEX_BASE_URL`.
- Reference Vault contains only uploaded papers and OpenAlex-imported paper metadata.
- Remove Notes and Web Scraping from active product scope.
- Use Docling for PDF/scanned PDF to Markdown.
- Use LlamaIndex + Pinecone for RAG.
- Use OpenThaiGPT for Workbench citation analysis.
- Render citations as `[author_name, paper_name]`.
- UI is 3 panels: left Reference Vault, middle Workbench, right Chat Assistant.

Read first if present:
- AGENTS.md
- docs/codex_reviews/v3_sqlite_openalex_docling_preflight_review.md
- _context_packs/*.md

Prior review context to verify:
- app.py was main Streamlit UI.
- database.py was SQLite-based.
- Notes/Web were active.
- web_scraper was imported.
- upload supported PDF/TXT/DOCX/DOC.
- Pinecone was already used.
- citation output was [Author, Year].

Hard rules:
- Make smallest safe change.
- Preserve function names where possible.
- Do not rewrite whole app.
- Run py_compile or smallest relevant check.

# Phase 02 — SQLite Reference Vault Schema

Modify `database.py` or data layer to add v3 SQLite tables and helpers.

Required tables:
- projects
- reference_vault_documents
- reference_vault_chunks
- openalex_search_cache
- citation_candidates
- citation_logs
- usage_logs if needed

Keep legacy tables. Do not delete old data. Add default project helper.
