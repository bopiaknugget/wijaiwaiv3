# AGENTS.md — Wijaiwai / Research Workbench v3

## Product Identity

Wijaiwai / วิจัยไว is an AI Research Workspace for Thai and English academic users.

Core positioning:

> AI Research Workspace สำหรับนักวิจัยไทย

Core message:

> อ่าน-ถาม-เขียน-ตรวจ งานวิจัยในที่เดียว

Wijaiwai is not a generic chatbot. It is a research workbench with a Reference Vault, Workbench editor, and Chat Assistant.

## Current Architecture Decision

- Keep SQLite. Do not migrate to PostgreSQL.
- Keep OpenAlex integration.
- Keep Reference Vault.
- Remove Notes and Web Scraping from active product scope.
- Reference Vault contains only uploaded papers and OpenAlex-imported paper metadata.
- Use Docling for PDF-to-Markdown ingestion.
- Use LlamaIndex + Pinecone for RAG.
- Use OpenThaiGPT for Workbench citation reasoning.
- Final citation format must be `[author_name, paper_name]`.
- UI must remain 3 panels: left Reference Vault, middle Workbench, right Chat Assistant.

## Environment Variables

OpenAlex variables must live in `.env`:

```env
OPEN_ALEX_API_KEY=...
OPEN_ALEX_BASE_URL=https://api.openalex.org
```

Other likely variables:

```env
PINECONE_API_KEY=...
PINECONE_INDEX_NAME=...
PINECONE_HOST=...
OPENTHAI_API_KEY=...
```

Do not hardcode API keys, base URLs, Pinecone hosts, or LLM secrets.

## OpenAlex Docs

Use these URLs for OpenAlex:

- https://developers.openalex.org/
- https://developers.openalex.org/quickstart

Treat OpenAlex as paper metadata discovery and enrichment. Do not assume full text exists for every work.

## Context Packs

Read only relevant files:

- `_context_packs/project_overview.md`
- `_context_packs/architecture.md`
- `_context_packs/data_model_sqlite.md`
- `_context_packs/reference_vault_rules.md`
- `_context_packs/openalex_integration.md`
- `_context_packs/docling_ingestion_rules.md`
- `_context_packs/llamaindex_pinecone_rag_rules.md`
- `_context_packs/workbench_citation_rules.md`
- `_context_packs/ui_rules.md`
- `_context_packs/decision_log.md`

## Prior Code Review Context To Preserve

A previous review reported that:

- `app.py` was the main Streamlit UI.
- Existing UI had Documents, Notes, and Web tabs.
- `database.py` was SQLite-based.
- SQLite path was old `Database/research_notes.db`.
- Notes and Web were active in UI/backend.
- `web_scraper` was imported in active app path.
- Existing upload supported PDF/TXT/DOCX/DOC.
- Existing RAG used Pinecone.
- Pinecone namespace was mostly scoped by `user_id`.
- Existing citation output was APA-like `[Author, Year]`.
- OpenThaiGPT already existed in generator/reviewer paths.

Verify current code before editing.

## Non-Negotiable Rules

- Keep SQLite.
- Do not migrate to PostgreSQL.
- Do not preserve Notes/Web in active UI or active ingestion.
- Do not fabricate citations.
- Do not let the LLM invent final citation text.
- Backend must render citations from verified Reference Vault metadata.
- Citation format must be `[author_name, paper_name]`.
- SQLite is source of truth.
- Pinecone is vector index only.
- Use Docling for uploaded paper conversion.
- Use LlamaIndex for chunking/indexing/retrieval orchestration.
- Use OpenThaiGPT for Workbench citation reasoning.
- Preserve existing function names where possible.
- Make the smallest safe change.

## Subagent Routing

Codex should be explicitly instructed to use subagents. Suggested routing:

- `reference_vault_reviewer`: Reference Vault behavior, citation trust, Notes/Web removal.
- `sqlite_data_worker`: SQLite schema/data-layer work.
- `docling_ingestion_worker`: Docling PDF/Markdown ingestion.
- `llamaindex_pinecone_worker`: LlamaIndex + Pinecone RAG.
- `openalex_worker`: OpenAlex API integration.
- `workbench_citation_guard`: Workbench citation analysis and verification.
- `ui_worker`: 3-panel UI and label updates.
