Run the pytest test suite for the Research Workbench.

```bash
# Standard — unit tests only, no Redis required, safe for CI
pytest tests/ -v --ignore=tests/integration_test.py --ignore=tests/locustfile.py --ignore=tests/app_for_locust.py

# Include integration tests (requires Redis at localhost:6379)
pytest tests/ -v --ignore=tests/locustfile.py --ignore=tests/app_for_locust.py

# Standalone test files
pytest test_token_caps.py test_section_from_docs.py -v

# Single test file
pytest tests/test_anti_abuse.py -v
```

**Test categories**:
- `tests/test_anti_abuse.py` — anti-abuse unit tests using `fakeredis` (no real Redis)
- `tests/integration_test.py` — real Redis on DB 15; auto-skips if Redis unreachable
- `test_token_caps.py` — token cap enforcement tests
- `test_section_from_docs.py` — document section extraction tests

**Dependencies**:
- Unit: `pip install fakeredis pytest`
- Integration: Redis running at `localhost:6379`

**Never included**:
- `rag_pipeline.py` — frozen legacy, no tests exist
- `benchmark.py` — run separately with `/benchmark`
