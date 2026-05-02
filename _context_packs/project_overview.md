# Wijaiwai Project Overview v3

Wijaiwai is an AI Research Workspace for Thai and English academic users.

Core message: อ่าน-ถาม-เขียน-ตรวจ งานวิจัยในที่เดียว

## Current Decision

- SQLite stays as the main local source of truth.
- OpenAlex stays for scholarly metadata discovery.
- Reference Vault stays as trusted citation source.
- Notes and Web Scraping are removed from active scope.
- Upload pipeline moves to Docling → Markdown.
- RAG pipeline moves to LlamaIndex + Pinecone.
- Workbench citation reasoning uses OpenThaiGPT.
- Final citation format is `[author_name, paper_name]`.

## Product Flow

```text
Reference Vault
  ↓
Direct upload paper or OpenAlex metadata import
  ↓
Docling converts PDF/scanned PDF to Markdown
  ↓
LlamaIndex creates chunks/nodes and indexing flow
  ↓
Pinecone stores vectors
  ↓
Workbench analyzes text
  ↓
OpenThaiGPT selects allowed citation IDs
  ↓
Backend renders [author_name, paper_name]
```

## 3-Panel UI

Left: upload/manage Reference Vault through OpenAlex or direct upload.

Middle: Workbench, text editor, document actions, edit/add/review/cite.

Right: Chat assistant.

## Must Not Do

- Do not migrate to PostgreSQL.
- Do not keep Notes/Web Scraping in active scope.
- Do not fabricate citations.
- Do not cite papers outside Reference Vault.
