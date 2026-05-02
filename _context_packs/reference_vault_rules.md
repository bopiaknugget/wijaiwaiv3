# Reference Vault Rules

Reference Vault is the trusted document and citation source.

## Allowed

- Direct paper upload
- OpenAlex metadata import/discovery

## Removed from active scope

- Notes
- Web scraping
- webpage ingestion

## Document status

- active
- metadata_only
- processing
- failed
- archived

OpenAlex-only imported records should be `metadata_only` unless full text/upload exists.

## Citation rule

Only cite papers that exist in Reference Vault. Final format:

```text
[author_name, paper_name]
```

Do not cite OpenAlex search results unless imported into Reference Vault.
