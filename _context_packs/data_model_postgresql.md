# PostgreSQL Data Model for Wijaiwai v2

## Purpose

This file defines the recommended PostgreSQL data model after replacing SQLite.

PostgreSQL is the source of truth for:

- users
- projects
- Reference Vault documents
- paper metadata
- chunks
- Workbench text
- Workbench analysis
- citation logs
- OpenAlex metadata cache
- usage logs

Related context:

- [[project_overview]] — `_context_packs/project_overview.md`
- [[architecture]] — `_context_packs/architecture.md`
- [[reference_vault_rules]] — `_context_packs/reference_vault_rules.md`
- [[workbench_citation_rules]] — `_context_packs/workbench_citation_rules.md`

---

## Core Data Rule

Every user-owned object should be scoped by:

```text
user_id
project_id
```

Reference documents should also include:

```text
document_id
```

This is required for privacy, retrieval filtering, citation verification, and cost tracking.

---

## Recommended Tables

### users

Stores authenticated user profile.

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    display_name TEXT,
    avatar_url TEXT,
    auth_provider TEXT,
    provider_user_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    last_login_at TIMESTAMPTZ
);
```

### projects

Stores user research projects/workspaces.

```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    pinecone_namespace TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### reference_documents

Stores uploaded documents in Reference Vault.

```sql
CREATE TABLE reference_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    title TEXT,
    filename TEXT NOT NULL,
    file_type TEXT,
    storage_path TEXT,
    file_hash TEXT,

    authors_json JSONB,
    author_display TEXT,
    publication_year INT,
    doi TEXT,
    openalex_work_id TEXT,
    source_name TEXT,

    extraction_status TEXT DEFAULT 'pending',
    embedding_status TEXT DEFAULT 'pending',

    summary TEXT,
    language TEXT,

    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### reference_chunks

Stores extracted chunks and metadata.

```sql
CREATE TABLE reference_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES reference_documents(id) ON DELETE CASCADE,

    chunk_index INT NOT NULL,
    parent_chunk_id UUID,
    content TEXT NOT NULL,
    content_preview TEXT,

    page_number INT,
    section_title TEXT,
    token_count INT,

    vector_id TEXT,
    embedding_model TEXT,
    embedding_provider TEXT,

    created_at TIMESTAMPTZ DEFAULT now()
);
```

### openalex_search_cache

Stores normalized OpenAlex search result metadata.

```sql
CREATE TABLE openalex_search_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,

    query TEXT NOT NULL,
    openalex_work_id TEXT,
    title TEXT,
    authors_json JSONB,
    author_display TEXT,
    publication_year INT,
    doi TEXT,
    source_name TEXT,
    abstract_text TEXT,
    raw_json JSONB,

    created_at TIMESTAMPTZ DEFAULT now()
);
```

### workbench_documents

Stores user text inside Workbench.

```sql
CREATE TABLE workbench_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    title TEXT,
    content TEXT NOT NULL,
    language TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### workbench_analysis_runs

Stores analysis request/response summary.

```sql
CREATE TABLE workbench_analysis_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workbench_document_id UUID REFERENCES workbench_documents(id) ON DELETE SET NULL,

    request_type TEXT NOT NULL,
    input_text_hash TEXT,
    model_provider TEXT,
    model_name TEXT,

    result_json JSONB,
    fallback_reason TEXT,

    input_tokens INT,
    output_tokens INT,
    estimated_cost NUMERIC,

    created_at TIMESTAMPTZ DEFAULT now()
);
```

### citation_candidates

Stores candidate citations selected from Reference Vault before LLM generation.

```sql
CREATE TABLE citation_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id UUID NOT NULL REFERENCES workbench_analysis_runs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    document_id UUID NOT NULL REFERENCES reference_documents(id) ON DELETE CASCADE,
    chunk_ids UUID[],
    citation_key TEXT NOT NULL,
    author_display TEXT,
    paper_name TEXT,
    relevance_score NUMERIC,
    evidence_excerpt TEXT,

    created_at TIMESTAMPTZ DEFAULT now()
);
```

### citation_logs

Stores final citations used in output.

```sql
CREATE TABLE citation_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    analysis_run_id UUID NOT NULL REFERENCES workbench_analysis_runs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    document_id UUID NOT NULL REFERENCES reference_documents(id) ON DELETE CASCADE,
    citation_text TEXT NOT NULL,
    author_display TEXT,
    paper_name TEXT,
    verification_status TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

### usage_logs

Stores token and cost usage.

```sql
CREATE TABLE usage_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,

    request_type TEXT,
    provider TEXT,
    model TEXT,
    input_tokens INT,
    output_tokens INT,
    estimated_cost NUMERIC,
    latency_ms INT,

    created_at TIMESTAMPTZ DEFAULT now()
);
```

---

## Removed Tables

Do not create these unless the product scope changes:

```text
notes
web_pages
scraped_pages
web_sources
sqlite_migration_temp
```

---

## Important Indexes

Recommended indexes:

```sql
CREATE INDEX idx_projects_user_id ON projects(user_id);

CREATE INDEX idx_reference_documents_user_project
ON reference_documents(user_id, project_id);

CREATE INDEX idx_reference_documents_doi
ON reference_documents(doi);

CREATE INDEX idx_reference_documents_openalex_work_id
ON reference_documents(openalex_work_id);

CREATE INDEX idx_reference_chunks_user_project_document
ON reference_chunks(user_id, project_id, document_id);

CREATE INDEX idx_workbench_documents_user_project
ON workbench_documents(user_id, project_id);

CREATE INDEX idx_workbench_analysis_runs_user_project
ON workbench_analysis_runs(user_id, project_id);

CREATE INDEX idx_citation_logs_user_project
ON citation_logs(user_id, project_id);
```

---

## Citation Key

Canonical final format:

```text
[author_name, paper_name]
```

Recommended internal citation key:

```text
{author_display} | {paper_name} | {document_id}
```

Do not rely on title alone because multiple papers can have similar titles.

---

## Migration from SQLite

Suggested migration steps:

```text
1. Identify existing SQLite tables and columns
2. Map old tables to PostgreSQL schema
3. Create PostgreSQL migrations
4. Export SQLite data
5. Transform records to new schema
6. Import users/projects/documents
7. Validate row counts
8. Validate user/project ownership
9. Disable SQLite write path
10. Remove or archive SQLite dependency
```

---

## Security Rule

Backend must enforce ownership in every query.

Example:

```sql
SELECT *
FROM reference_documents
WHERE id = $1
  AND user_id = $2
  AND project_id = $3;
```

Never fetch by `document_id` alone.

---

## Definition of Done

PostgreSQL migration is acceptable when:

```text
[ ] User/project data stored in PostgreSQL
[ ] Reference Vault document metadata stored in PostgreSQL
[ ] Chunk metadata stored in PostgreSQL
[ ] Workbench text stored in PostgreSQL
[ ] Citation logs stored in PostgreSQL
[ ] SQLite is not used as main persistent DB
[ ] All data queries enforce user_id/project_id ownership
```
