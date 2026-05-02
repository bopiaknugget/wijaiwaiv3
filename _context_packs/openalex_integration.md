# OpenAlex Integration

Official URLs:

- https://developers.openalex.org/
- https://developers.openalex.org/quickstart

## Environment Variables

```env
OPEN_ALEX_API_KEY=...
OPEN_ALEX_BASE_URL=https://api.openalex.org
```

Use `OPEN_ALEX_API_KEY` as `api_key` query parameter when calling OpenAlex.

## Expected Functions

- `search_works(query, per_page=10)`
- `get_work_by_doi(doi)`
- `normalize_work(raw_work)`
- `cache_openalex_search(query, response)`
- `import_openalex_work_to_reference_vault(...)`

## Normalized fields

```json
{
  "openalex_id": "...",
  "doi": "...",
  "paper_name": "...",
  "author_display": "...",
  "authors": [],
  "publication_year": 2024,
  "source_name": "...",
  "landing_page_url": "...",
  "pdf_url": "...",
  "is_open_access": true,
  "has_fulltext": false,
  "raw": {}
}
```

Do not fabricate missing metadata.
