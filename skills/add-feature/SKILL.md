# Skill: add-feature

Structured workflow for adding features to the Research Workbench. Follow each phase in order.

## Phase 1: Read Before Writing

Before touching any code:

1. Read `CLAUDE.md` for architecture, pitfalls, and agent routing
2. Read the modules you'll touch (`app.py`, `database.py`, `vector_store.py`, etc.)
3. Identify: which SQLite tables are affected? Which Pinecone namespaces?
4. Confirm `rag_pipeline.py` is NOT involved — it is frozen
5. Identify existing patterns to reuse (e.g., Value Proxy for Streamlit inputs)

## Phase 2: Plan

Write a short plan covering:
- **Data model**: new SQLite table/columns needed?
- **Pinecone impact**: new `source_type`? new metadata fields?
- **UI**: which panel? new `st.session_state` keys?
- **User isolation**: how does `user_id` flow through?
- **Error cases**: what fails gracefully vs. hard errors?

## Phase 3: Implement in Order

**Always implement in this sequence**: DB → backend logic → routing → UI

1. **`database.py`** first: schema changes, new CRUD functions, migration via `_add_column_if_missing()`
2. **Backend modules** (`vector_store.py`, `generator.py`, or new module): business logic
3. **`query_router.py`**: only if new intent type is needed
4. **`app.py`** last: Streamlit integration

### Streamlit implementation checklist:
- [ ] New widgets use unique `key=` values
- [ ] Widget values needing programmatic reset: use Value Proxy pattern (`*_val` key + `st.rerun()`)
- [ ] `user_id = st.session_state.get('user_id')` at top of any data op
- [ ] Pass `user_id` to all SQLite and Pinecone calls
- [ ] Handle unauthenticated state (`user_id` may be None before OAuth)

## Phase 4: Test

```bash
# Run unit tests
pytest tests/ -v --ignore=tests/integration_test.py --ignore=tests/locustfile.py

# Smoke test the UI
streamlit run app.py
```

Manual test checklist:
- [ ] Feature works for authenticated user
- [ ] Feature does NOT expose another user's data
- [ ] Edge cases: empty input, very long input, Thai characters
- [ ] No new Streamlit widget key conflicts
- [ ] Token usage recorded if LLM was called (`record_token_usage()`)

## Phase 5: Update Documentation

1. Update `CLAUDE.md`:
   - New module → add to Module Map table
   - New env var → add to Environment section
   - New pitfall → add to Common Pitfalls
   - New agent routing case → add to Agent Routing Guide

2. Update agent definitions if scope changed:
   - New SQLite table → note in `sqlite-db-agent.md` schema section
   - New UI pattern → note in `streamlit-ui-expert.md`

## Safeguards

- Never modify `rag_pipeline.py`
- Never query Pinecone without a namespace
- Never write editor content to filesystem (use SQLite `editor_documents`)
- Call `record_token_usage()` for every LLM call in the new feature
- Per-user isolation is non-negotiable — test it explicitly
