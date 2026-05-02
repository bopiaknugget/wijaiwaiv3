Ingest a document into the Pinecone vector store via the CLI.

```bash
# Ingest a PDF for a specific user
python main.py --ingest path/to/document.pdf --user USER_ID

# Ingest using default CLI namespace
python main.py --ingest path/to/document.pdf

# Verify retrieval after ingest
python main.py --query "describe the main topic" --user USER_ID
```

**Arguments**:
- `--ingest PDF_PATH` — path to PDF, TXT, or DOCX file
- `--user USER_ID` — Pinecone namespace / user ID (default: `cli_user`)
- `--k K` — retrieval result count in query mode (default: 3)

**What happens during ingest**:
1. `load_pdf_document()` — parses the file
2. `enrich_metadata()` — extracts title, authors, year, section
3. `create_parent_child_chunks()` — splits into child (Pinecone) and parent (SQLite) chunks
4. `get_embedding_model()` — initializes Pinecone Inference API
5. `ingest_documents()` — upserts child chunks to Pinecone, stores parents in SQLite `parent_chunks`

**Important**:
- `USER_ID` must match a Google user ID to be visible from the web UI
- Default `cli_user` namespace is only accessible via CLI query
- SHA-256 embedding cache skips re-embedding identical content
- Supported formats: PDF, TXT, DOCX
