# RAG Rules for Wijaiwai v2

## Purpose

This file defines retrieval and grounding rules after the new product scope.

RAG now focuses on **Reference Vault retrieval** for Workbench citation analysis.

Related context:

- [[project_overview]] — `_context_packs/project_overview.md`
- [[architecture]] — `_context_packs/architecture.md`
- [[reference_vault_rules]] — `_context_packs/reference_vault_rules.md`
- [[workbench_citation_rules]] — `_context_packs/workbench_citation_rules.md`

---

## Core Rule

RAG must retrieve evidence only from the current user's current project Reference Vault.

The system must not retrieve from:

- another user
- another project
- deleted documents
- OpenAlex metadata-only results
- scraped web content
- notes

---

## RAG Scope

Current RAG use cases:

```text
1. Workbench text analysis
2. Citation suggestion from Vault
3. Claim support checking
4. Reference document Q&A if retained
5. Paper relevance explanation
```

Removed RAG sources:

```text
notes
web_scraping
general_web_pages
```

---

## Retrieval Flow

```text
Workbench text or user question
 ↓
Extract query / claims / keywords
 ↓
Apply user_id and project_id scope
 ↓
Search Reference Vault chunks
 ↓
Rank candidate chunks
 ↓
Group by document/paper
 ↓
Build citation candidates
 ↓
Pass candidates to OpenThaiGPT
 ↓
Verify citations
```

---

## Candidate Selection

Candidate citations should be based on:

- chunk relevance
- document relevance
- title/abstract/section match
- exact keyword match
- semantic similarity
- metadata match
- source reliability inside Vault

Do not select too many candidates.

Recommended starting point:

```text
candidate_chunks = 8 to 12
candidate_papers = 3 to 6
final_citations = 0 to 5
```

Tune based on quality and latency.

---

## Citation Candidate Format

Each candidate should include:

```text
citation_id
document_id
chunk_ids
author_display
paper_name
year
evidence_excerpt
relevance_score
```

The LLM must cite by `citation_id`, not by free-form text.

Backend renders:

```text
[author_display, paper_name]
```

---

## Weak Context Rule

If retrieved context is weak:

```text
Do not cite.
```

Fallback:

```text
ยังไม่พบ paper ใน Reference Vault ที่รองรับประเด็นนี้ได้อย่างเพียงพอ
```

---

## Deduplication

Before passing candidates to OpenThaiGPT:

- group chunks by document
- remove duplicate chunks
- prefer strongest evidence from each paper
- avoid multiple citations to same paper unless necessary
- prefer verified metadata

---

## Metadata Preservation

RAG must preserve:

```text
document_id
chunk_id
author_display
paper_name
page_number
section_title
doi
openalex_work_id
```

If metadata is incomplete, do not invent it.

---

## Source of Truth

PostgreSQL is the source of truth.

Vector store results must be verified against PostgreSQL before citation.

Recommended verification:

```text
1. vector result returns vector_id/document_id/chunk_id
2. backend fetches chunk/document from PostgreSQL
3. backend verifies user_id/project_id
4. backend builds candidate citation from PostgreSQL metadata
```

---

## OpenThaiGPT Prompt Rule

The prompt should say:

```text
Use only the allowed citation candidates.
If none support the claim, say no sufficient Vault evidence.
Do not create new references.
Final citation format must be [author_name, paper_name].
```

---

## Hybrid Retrieval

If available, use:

```text
BM25 + vector retrieval
```

Why:

- BM25 catches exact paper terms and author names
- vector search catches semantic similarity
- combined retrieval improves academic matching

Use simple fusion first.

---

## Evaluation Checklist

```text
[ ] Retrieval is scoped by user_id/project_id
[ ] Retrieval uses only Reference Vault
[ ] OpenAlex metadata-only results are not cited
[ ] Candidate citations map to PostgreSQL records
[ ] OpenThaiGPT receives only allowed candidates
[ ] Final citations use [author_name, paper_name]
[ ] Unsupported text gets fallback
[ ] No fake author or paper title appears
```
