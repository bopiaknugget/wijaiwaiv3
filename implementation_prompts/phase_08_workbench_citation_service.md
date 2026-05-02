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

# Phase 08 — Workbench Citation Service

Implement Workbench citation analysis using OpenThaiGPT and Reference Vault only.

Flow:
1. Read Workbench text
2. Retrieve Vault candidates from LlamaIndex/Pinecone
3. Verify in SQLite
4. Build allowed citation IDs
5. OpenThaiGPT selects IDs only
6. Reject unknown IDs
7. Backend renders `[author_name, paper_name]`
8. Save citation log

Do not let LLM invent final citation text.
