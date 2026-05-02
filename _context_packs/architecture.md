# Wijaiwai Architecture v3

## Summary

```text
Streamlit 3-panel UI
  ↓
Reference Vault / Workbench / Chat Assistant
  ↓
SQLite source of truth
  ↓
Docling PDF-to-Markdown ingestion
  ↓
LlamaIndex chunking + embedding + indexing orchestration
  ↓
Pinecone vector database
  ↓
OpenThaiGPT Workbench analysis
  ↓
Backend-rendered citations
```

## Components

### SQLite

SQLite stores trusted data: users, projects/default project, Reference Vault documents, chunks, OpenAlex cache, Workbench documents, citation logs, usage logs.

### Reference Vault

Only contains uploaded papers and OpenAlex-imported paper metadata. Notes/Web are not active sources.

### Docling

Converts uploaded PDFs, especially scanned PDFs, to high-quality Markdown.

### LlamaIndex

Handles document/nodes/chunks, embedding orchestration, Pinecone indexing and retrieval where useful.

### Pinecone

Vector DB only. Not source of truth. Store minimal metadata: user_id, project_id, document_id, chunk_id, source_type=reference_document.

### OpenAlex

Metadata discovery and enrichment. Env vars: `OPEN_ALEX_API_KEY`, `OPEN_ALEX_BASE_URL`.

### OpenThaiGPT

Analyzes Workbench text and selects allowed citation IDs. Backend renders final citation text.

## Strict Citation Flow

```text
Workbench text
  ↓
Retrieve Vault candidates from Pinecone/LlamaIndex
  ↓
Verify candidates against SQLite
  ↓
Build allowed citation IDs
  ↓
OpenThaiGPT selects IDs only
  ↓
Backend renders [author_name, paper_name]
```
