# Wijaiwai Context + Codex Agent Package v3

Updated decision package:

- Keep SQLite. Do not migrate to PostgreSQL.
- Keep OpenAlex integration.
- OpenAlex env vars: `OPEN_ALEX_API_KEY`, `OPEN_ALEX_BASE_URL`.
- Reference Vault remains, but only for uploaded papers and OpenAlex-imported paper metadata.
- Remove Notes and Web Scraping from active product scope.
- Replace the old document parser path with Docling.
- Use LlamaIndex + Pinecone for RAG.
- Use OpenThaiGPT for Workbench citation analysis.
- Final citation format: `[author_name, paper_name]`.
- Keep 3-panel UI:
  - Left: upload/manage Reference Vault through OpenAlex or direct upload
  - Middle: Workbench text editor and document actions/edit/add/review
  - Right: Chat assistant

## Files

```text
AGENTS.md
.codex/agents/*.toml
_context_packs/*.md
implementation_prompts/*.md
```

## Usage

1. Copy `AGENTS.md` to the repo root.
2. Copy `.codex/agents/` to the repo root if using Codex subagents.
3. Copy `_context_packs/` to the repo root and/or Obsidian Vault.
4. Run prompts in `implementation_prompts/` one phase at a time.

Start with:

```text
implementation_prompts/phase_00_preflight_review_no_edit.md
```

Phase 00 must create:

```text
docs/codex_reviews/v3_sqlite_openalex_docling_preflight_review.md
```

Later phases must read that review report before editing.
