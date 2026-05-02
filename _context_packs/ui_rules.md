# UI Rules

Required 3-panel layout:

```text
Left: Reference Vault
Middle: Workbench
Right: Chat Assistant
```

## Left panel

- Upload paper
- OpenAlex search/import
- Vault paper list
- processing status
- metadata-only warning

## Middle panel

- text editor
- edit/add/review/cite actions
- citation suggestions
- unsupported claim warnings

## Right panel

- chat assistant using Reference Vault RAG
- fallback when Vault has insufficient information

Do not expose active Notes or Web Scraping UI.
