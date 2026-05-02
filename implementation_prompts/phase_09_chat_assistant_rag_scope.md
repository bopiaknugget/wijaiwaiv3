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

# Phase 09 — Chat Assistant RAG Scope

Ensure right-panel Chat Assistant uses Reference Vault RAG only.

Required:
- no Notes/Web retrieval
- no metadata-only OpenAlex as full-text evidence
- fallback when Vault context is insufficient
- preserve existing OpenThaiGPT chat path if already working
