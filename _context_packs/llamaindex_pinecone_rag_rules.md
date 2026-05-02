# LlamaIndex + Pinecone RAG Rules

Roles:

```text
Docling = PDF to Markdown
LlamaIndex = chunks/nodes/embedding/indexing/retrieval orchestration
Pinecone = vector database
SQLite = source of truth
OpenThaiGPT = reasoning and answer/citation analysis
```

## Metadata for indexed chunks

```json
{
  "user_id": "...",
  "project_id": "...",
  "document_id": "...",
  "chunk_id": "...",
  "source_type": "reference_document"
}
```

## Strict retrieval for Workbench citation

- Reference Vault only
- active/content-backed documents only
- verify chunk/document in SQLite
- do not retrieve Notes/Web
- do not broaden source filters in fallback
