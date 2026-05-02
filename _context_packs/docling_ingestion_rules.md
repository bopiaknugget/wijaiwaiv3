# Docling Ingestion Rules

Docling should replace the old paper parsing path.

## Flow

```text
Uploaded PDF/scanned PDF
  ↓
Docling conversion
  ↓
Markdown output
  ↓
metadata extraction / optional OpenAlex enrichment
  ↓
LlamaIndex nodes/chunks
  ↓
SQLite chunks + Pinecone vectors
```

## Store artifacts

```text
storage/reference_vault/{user_id}/{project_id}/{document_id}/original.pdf
storage/reference_vault/{user_id}/{project_id}/{document_id}/docling.md
storage/reference_vault/{user_id}/{project_id}/{document_id}/docling_meta.json
```

If Docling fails, mark document failed and do not index broken chunks.
