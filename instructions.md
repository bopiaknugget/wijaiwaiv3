Superseded note (2026-05-03): The current architecture decision is to keep SQLite.
Do not replace SQLite with PostgreSQL. Use AGENTS.md and `_context_packs/data_model_sqlite.md`
for current database work. The PostgreSQL references below are historical.

Review the current Wijaiwai codebase against the new architecture only. Do not edit files yet.

New requirements:
1. Replace SQLite with PostgreSQL.
2. Add OpenAlex paper/document metadata discovery.
3. Remove Notes and Web Scraping from active product scope.
4. Rename uploaded documents area to Reference Vault.
5. Workbench must analyze text and cite papers from Reference Vault in format [author_name, paper_name] using OpenThaiGPT.

Read:
- AGENTS.md
- _context_packs/project_overview.md
- _context_packs/architecture.md
- _context_packs/data_model_postgresql.md
- _context_packs/reference_vault_rules.md
- _context_packs/openalex_integration.md
- _context_packs/workbench_citation_rules.md
- _context_packs/rag_rules.md
- _context_packs/ui_rules.md

Return:
1. Current architecture summary
2. Gap analysis vs new architecture
3. Files that need changes
4. Minimal refactor plan
5. Risk assessment
6. Suggested implementation order

Report output : 0_change_architecture_report.md

Do not edit files.
Do not refactor yet.
Focus on the smallest safe migration path.
