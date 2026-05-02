# Workbench Citation Rules

Workbench citation format:

```text
[author_name, paper_name]
```

## OpenThaiGPT role

OpenThaiGPT may select allowed citation IDs only.

Expected LLM output:

```json
{"citations":[{"citation_id":"CIT_001","reason":"..."}]}
```

Backend must validate IDs and render citation text from SQLite metadata.

## Fallback

If no reliable Vault paper supports the text:

```text
ยังไม่พบ paper ใน Reference Vault ที่รองรับข้อความนี้อย่างเพียงพอ
```

Do not let LLM invent final citations.
