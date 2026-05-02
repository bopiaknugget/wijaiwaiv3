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

# Phase 00 — Preflight Review, No Product Refactor

Task: review code and create exactly one report file:

`docs/codex_reviews/v3_sqlite_openalex_docling_preflight_review.md`

Do not modify product code.

Report must include:
1. Current architecture detected from code
2. SQLite usage
3. Upload/document parser flow
4. Pinecone/RAG flow
5. Citation generation flow
6. OpenThaiGPT call paths
7. Notes/Web active paths
8. Env/config usage
9. Files that need changes per phase
10. Diff-style plan: keep/remove-hide/replace/add
11. Risks and minimal order
