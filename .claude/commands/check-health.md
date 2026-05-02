Verify the Research Workbench environment is correctly configured.

```bash
python -c "
import os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

required = [
    'OPENTHAI_API_KEY', 'PINECONE_API_KEY', 'PINECONE_INDEX_NAME',
    'PINECONE_HOST', 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'GOOGLE_REDIRECT_URI',
]
missing = [k for k in required if not os.getenv(k)]
if missing:
    print('MISSING env vars:', missing)
    sys.exit(1)
print('OK: All env vars present')

modules = ['generator', 'vector_store', 'document_loader', 'database', 'auth', 'reviewer', 'web_scraper', 'query_router']
failed = []
for m in modules:
    try:
        __import__(m)
        print(f'OK: {m}')
    except Exception as e:
        print(f'FAIL: {m} — {e}')
        failed.append(m)

from database import initialize_database
initialize_database()
db_path = Path('Database/research_notes.db')
size = db_path.stat().st_size if db_path.exists() else 0
print(f'OK: SQLite DB ({size} bytes)' if db_path.exists() else 'WARN: DB not yet created (will auto-create on app start)')

if failed:
    print(f'\nFailed imports: {failed}')
    sys.exit(1)
print('\nHealth check passed.')
"
```

**Checks**:
1. All required `.env` vars are set and non-empty
2. Core Python modules import without error
3. SQLite DB is initialized and accessible

**Common failures**:
- Missing `OPENTHAI_API_KEY` → LLM calls fail at runtime
- Missing `PINECONE_HOST` → vector store init fails
- Import error in any module → usually missing `pip install` dep
- DB missing → not an error; `initialize_database()` creates it on first run
