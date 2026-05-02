Run flake8 linting on the Research Workbench source.

```bash
flake8 . --exclude=venv,pvenv,.venv,__pycache__,.claude,Database,user_data,rag_pipeline.py --max-line-length=100
```

**Excludes**:
- `venv/`, `pvenv/`, `.venv/` — virtual environments
- `__pycache__/` — compiled cache
- `.claude/` — Claude config files
- `Database/` — SQLite data directory
- `user_data/` — legacy imports
- `rag_pipeline.py` — frozen legacy, not maintained for style

**Line length**: 100 chars (accommodates Streamlit's verbose widget calls).

**Common findings**:
- Unused imports in `app.py` — Streamlit apps accumulate these
- Long SQL strings in `database.py` — suppress with `# noqa: E501`
- f-string SQL in old code — flag as security issue (SQL injection), not just style

**Auto-fix import sorting**:
```bash
isort . --skip venv --skip pvenv --skip .venv --skip rag_pipeline.py
```
