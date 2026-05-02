# SQLite Data Model v3

SQLite remains source of truth. Do not migrate to PostgreSQL.

## Recommended New Tables

- projects
- reference_vault_documents
- reference_vault_chunks
- workbench_documents
- openalex_search_cache
- citation_candidates
- citation_logs
- usage_logs

## reference_vault_documents fields

```text
id, user_id, project_id, source_origin, status,
paper_name, author_display, authors_json, publication_year,
doi, openalex_id, source_name, landing_page_url, pdf_url,
filename, file_type, storage_path,
docling_markdown_path, docling_status, extraction_metadata_json,
summary, metadata_json, created_at, updated_at
```

## reference_vault_chunks fields

```text
id, document_id, user_id, project_id, chunk_index, parent_chunk_id,
content, content_hash, page_number, section_title,
node_metadata_json, pinecone_vector_id, embedding_model,
source_type='reference_document', created_at
```

## Legacy Tables

Old tables such as `research_notes`, `documents`, `parent_chunks`, `web_pages` may remain for compatibility, but v3 active paths should use Reference Vault tables.
