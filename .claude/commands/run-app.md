Start the WijaiWai Research Workbench Streamlit UI.

```bash
streamlit run app.py
```

**Prerequisites**:
- `.env` present with all keys: `OPENTHAI_API_KEY`, `PINECONE_API_KEY`, `PINECONE_INDEX_NAME`, `PINECONE_HOST`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`
- `pip install -r requirements.txt` already run
- Runs at: http://localhost:8501

**What happens on start**:
- `initialize_database()` creates `./Database/research_notes.db` and all 7 tables if missing
- Google OAuth splash screen shown before any app access
- `st.session_state` initialized for chat, editor, and user context

**Common issues**:
- Port in use: `streamlit run app.py --server.port 8502`
- Widget key error: each `st.widget()` must have a unique `key=` arg
- OAuth redirect mismatch: `GOOGLE_REDIRECT_URI` must exactly match Google Console URI
- Import error: run `/check-health` to diagnose
