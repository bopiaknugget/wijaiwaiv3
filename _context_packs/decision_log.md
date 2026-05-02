# Decision Log

## Keep SQLite

PostgreSQL migration is cancelled for now. SQLite remains the source of truth.

## Keep OpenAlex

Use OpenAlex with env vars:

```env
OPEN_ALEX_API_KEY=...
OPEN_ALEX_BASE_URL=https://api.openalex.org
```

## Reference Vault scope

Allowed: direct uploaded papers and OpenAlex metadata import.

Removed: Notes, Web Scraping, webpage ingestion.

## RAG pipeline

```text
Docling → LlamaIndex → Pinecone → OpenThaiGPT
```

## UI

Left Reference Vault, Middle Workbench, Right Chat Assistant.
