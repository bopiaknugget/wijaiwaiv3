from pathlib import Path


RUNTIME_PY_FILES = [
    path for path in Path(".").glob("*.py")
    if path.name not in {"database.py"}
]


def test_runtime_sqlite_connections_go_through_database_module():
    offenders = []
    for path in RUNTIME_PY_FILES:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "sqlite3.connect" in text:
            offenders.append(str(path))

    assert offenders == []


def test_runtime_has_no_postgresql_database_path():
    patterns = [
        "psycopg",
        "psycopg2",
        "asyncpg",
        "DATABASE_URL",
        "db_postgres",
        "database_postgres",
    ]
    offenders = []
    for path in RUNTIME_PY_FILES + [Path("requirements.txt")]:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        lower_text = text.lower()
        for pattern in patterns:
            if pattern.lower() in lower_text:
                offenders.append(f"{path}:{pattern}")

    assert offenders == []
