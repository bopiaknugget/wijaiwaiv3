"""
Database Module for Research Workbench
Handles storage and retrieval of research notes, document metadata,
and parent chunks for Parent-Child Chunking (Advanced RAG).
Uses context managers for all connections to prevent locks and leaks.

User isolation: documents, notes, and web_pages are all scoped to user_id.
Migration: ALTER TABLE adds user_id columns if they don't exist on startup.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


DB_PATH = Path(__file__).parent / "Database" / "research_notes.db"


def _utc_now() -> str:
    """Return an ISO timestamp for new v3 records."""
    return datetime.utcnow().isoformat()


def _new_id() -> str:
    """Return a string ID for v3 SQLite tables."""
    return uuid.uuid4().hex


def _json_text(value) -> str | None:
    """Store JSON-compatible objects as TEXT while preserving string callers."""
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _row_to_dict(cursor, row) -> dict | None:
    """Convert a sqlite row tuple to a dict using cursor metadata."""
    if row is None:
        return None
    columns = [column[0] for column in cursor.description]
    return dict(zip(columns, row))


@contextmanager
def get_db_connection():
    """
    Context manager for SQLite connections.
    Guarantees the connection is closed even if an exception is raised.

    Usage:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            ...
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        yield conn
    finally:
        conn.close()


def initialize_database():
    """
    Create tables if they don't exist. Safe to call on every startup.
    Does NOT drop existing tables — data is fully persistent.
    Also runs migrations to add user_id columns if missing.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Research notes table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS research_notes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                title     TEXT NOT NULL,
                content   TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Uploaded document metadata table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS documents (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT NOT NULL,
                file_type   TEXT,
                chunk_count INTEGER DEFAULT 0,
                db_path     TEXT,
                timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Parent chunks table for Parent-Child Chunking (Advanced RAG)
        # Child chunks in Pinecone reference parent_id to retrieve larger context
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS parent_chunks (
                id          TEXT PRIMARY KEY,
                content     TEXT NOT NULL,
                source_file TEXT,
                page_number INTEGER,
                section     TEXT,
                timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Web pages table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS web_pages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT NOT NULL,
                title       TEXT NOT NULL,
                summary     TEXT NOT NULL,
                chunk_count INTEGER DEFAULT 0,
                timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Token usage table — tracks per-session/turn input and output tokens
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS token_usage (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        TEXT,
                input_tokens   INTEGER DEFAULT 0,
                output_tokens  INTEGER DEFAULT 0,
                function_name  TEXT,
                timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Users table for Google OAuth
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id         TEXT PRIMARY KEY,
                email      TEXT UNIQUE NOT NULL,
                name       TEXT,
                picture    TEXT,
                created_at TEXT,
                last_login TEXT
            )
        ''')

        # v3 projects table. Legacy tables remain untouched.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                name       TEXT,
                is_default INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
        ''')

        # v3 Reference Vault document metadata.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reference_vault_documents (
                document_id              TEXT PRIMARY KEY,
                user_id                  TEXT NOT NULL,
                project_id               TEXT NOT NULL,
                paper_name               TEXT,
                author_display           TEXT,
                authors_json             TEXT,
                doi                      TEXT,
                openalex_id              TEXT,
                publication_year         INTEGER,
                filename                 TEXT,
                file_type                TEXT,
                storage_path             TEXT,
                docling_markdown_path    TEXT,
                docling_status           TEXT,
                source_type              TEXT NOT NULL DEFAULT 'reference_document',
                status                   TEXT NOT NULL DEFAULT 'active',
                metadata_json            TEXT,
                extraction_metadata_json TEXT,
                created_at               TEXT,
                updated_at               TEXT
            )
        ''')

        # v3 Reference Vault chunks. Pinecone stores vectors only.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reference_vault_chunks (
                chunk_id           TEXT PRIMARY KEY,
                document_id        TEXT NOT NULL,
                user_id            TEXT NOT NULL,
                project_id         TEXT NOT NULL,
                parent_chunk_id    TEXT,
                chunk_index        INTEGER NOT NULL,
                markdown_content   TEXT,
                content            TEXT,
                content_hash       TEXT,
                page_number        INTEGER,
                section_title      TEXT,
                pinecone_vector_id TEXT,
                embedding_model    TEXT,
                source_type        TEXT DEFAULT 'reference_document',
                metadata_json      TEXT,
                created_at         TEXT
            )
        ''')

        # OpenAlex cache is metadata-only; no OpenAlex API logic lives here.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS openalex_search_cache (
                cache_id      TEXT PRIMARY KEY,
                user_id       TEXT,
                query         TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at    TEXT
            )
        ''')

        # Citation candidate/log tables support later verified citation flow.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS citation_candidates (
                candidate_id     TEXT PRIMARY KEY,
                user_id          TEXT NOT NULL,
                project_id       TEXT NOT NULL,
                document_id      TEXT NOT NULL,
                chunk_id         TEXT,
                author_display   TEXT,
                paper_name       TEXT,
                evidence_excerpt TEXT,
                metadata_json    TEXT,
                created_at       TEXT
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS citation_logs (
                citation_log_id      TEXT PRIMARY KEY,
                user_id              TEXT NOT NULL,
                project_id           TEXT NOT NULL,
                workbench_document_id TEXT,
                document_id          TEXT,
                chunk_id             TEXT,
                citation_text        TEXT,
                citation_format      TEXT DEFAULT '[author_name, paper_name]',
                metadata_json        TEXT,
                created_at           TEXT
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id)')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_projects_user_default '
            'ON projects(user_id, is_default)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_rv_documents_user_project '
            'ON reference_vault_documents(user_id, project_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_rv_documents_user_project_status '
            'ON reference_vault_documents(user_id, project_id, status)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_rv_documents_doi '
            'ON reference_vault_documents(doi)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_rv_documents_openalex_id '
            'ON reference_vault_documents(openalex_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_rv_chunks_document_id '
            'ON reference_vault_chunks(document_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_rv_chunks_user_project '
            'ON reference_vault_chunks(user_id, project_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_rv_chunks_pinecone_vector_id '
            'ON reference_vault_chunks(pinecone_vector_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_openalex_cache_user_query '
            'ON openalex_search_cache(user_id, query)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_citation_candidates_user_project '
            'ON citation_candidates(user_id, project_id)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_citation_logs_user_project '
            'ON citation_logs(user_id, project_id)'
        )

        conn.commit()

        # ── Migrations: add user_id columns if not present ─────────────────
        _add_column_if_missing(cursor, "documents",      "user_id", "TEXT")
        _add_column_if_missing(cursor, "research_notes", "user_id", "TEXT")
        _add_column_if_missing(cursor, "web_pages",      "user_id", "TEXT")
        conn.commit()


def _add_column_if_missing(cursor, table: str, column: str, col_type: str):
    """Add a column to a table only if it doesn't already exist."""
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


# ── v3 Projects ─────────────────────────────────────────────────────────────

def create_default_project(user_id: str) -> str:
    """Create or return the default project for a user."""
    existing_project_id = get_default_project_id(user_id)
    if existing_project_id:
        return existing_project_id

    now = _utc_now()
    project_id = _new_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO projects (project_id, user_id, name, is_default, created_at, updated_at) '
            'VALUES (?, ?, ?, 1, ?, ?)',
            (project_id, user_id, 'Default Project', now, now)
        )
        conn.commit()
    return project_id


def get_default_project_id(user_id: str) -> str | None:
    """Return the default project ID for a user, if it exists."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT project_id FROM projects WHERE user_id = ? AND is_default = 1 '
            'ORDER BY created_at ASC LIMIT 1',
            (user_id,)
        )
        row = cursor.fetchone()
        return row[0] if row else None


# ── v3 Reference Vault Documents ────────────────────────────────────────────

def save_reference_vault_document(
    document_id: str = None,
    user_id: str = None,
    project_id: str = None,
    paper_name: str = None,
    author_display: str = None,
    authors_json=None,
    doi: str = None,
    openalex_id: str = None,
    publication_year: int = None,
    filename: str = None,
    file_type: str = None,
    storage_path: str = None,
    docling_markdown_path: str = None,
    docling_status: str = None,
    source_type: str = 'reference_document',
    status: str = 'active',
    metadata_json=None,
    extraction_metadata_json=None,
) -> str:
    """Insert or replace a Reference Vault document and return its string ID."""
    if not user_id:
        raise ValueError("user_id is required")
    if not project_id:
        project_id = create_default_project(user_id)

    now = _utc_now()
    document_id = document_id or _new_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT created_at FROM reference_vault_documents WHERE document_id = ?',
            (document_id,)
        )
        row = cursor.fetchone()
        created_at = row[0] if row else now
        cursor.execute(
            '''
            INSERT OR REPLACE INTO reference_vault_documents (
                document_id, user_id, project_id, paper_name, author_display,
                authors_json, doi, openalex_id, publication_year, filename,
                file_type, storage_path, docling_markdown_path, docling_status,
                source_type, status, metadata_json, extraction_metadata_json,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                document_id, user_id, project_id, paper_name, author_display,
                _json_text(authors_json), doi, openalex_id, publication_year,
                filename, file_type, storage_path, docling_markdown_path,
                docling_status, source_type, status, _json_text(metadata_json),
                _json_text(extraction_metadata_json), created_at, now,
            )
        )
        conn.commit()
    return document_id


def get_reference_vault_document(
    document_id: str,
    user_id: str = None,
    project_id: str = None,
) -> dict | None:
    """Retrieve a Reference Vault document by ID with optional user/project scope."""
    query = 'SELECT * FROM reference_vault_documents WHERE document_id = ?'
    params = [document_id]
    if user_id is not None:
        query += ' AND user_id = ?'
        params.append(user_id)
    if project_id is not None:
        query += ' AND project_id = ?'
        params.append(project_id)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return _row_to_dict(cursor, cursor.fetchone())


def list_reference_vault_documents(
    user_id: str,
    project_id: str = None,
    status: str = None,
) -> list:
    """List Reference Vault documents for a user with optional filters."""
    query = 'SELECT * FROM reference_vault_documents WHERE user_id = ?'
    params = [user_id]
    if project_id is not None:
        query += ' AND project_id = ?'
        params.append(project_id)
    if status is not None:
        query += ' AND status = ?'
        params.append(status)
    query += ' ORDER BY created_at DESC'

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [_row_to_dict(cursor, row) for row in cursor.fetchall()]


def update_reference_vault_document_status(
    document_id: str,
    status: str,
    metadata_json=None,
) -> bool:
    """Update Reference Vault document status and optional metadata."""
    now = _utc_now()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if metadata_json is None:
            cursor.execute(
                'UPDATE reference_vault_documents SET status = ?, updated_at = ? '
                'WHERE document_id = ?',
                (status, now, document_id)
            )
        else:
            cursor.execute(
                'UPDATE reference_vault_documents '
                'SET status = ?, metadata_json = ?, updated_at = ? WHERE document_id = ?',
                (status, _json_text(metadata_json), now, document_id)
            )
        conn.commit()
        return cursor.rowcount > 0


def delete_reference_vault_document(document_id: str, user_id: str = None) -> bool:
    """Archive a Reference Vault document without deleting the SQLite record."""
    query = (
        'UPDATE reference_vault_documents SET status = ?, updated_at = ? '
        'WHERE document_id = ?'
    )
    params = ['archived', _utc_now(), document_id]
    if user_id is not None:
        query += ' AND user_id = ?'
        params.append(user_id)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor.rowcount > 0


# ── v3 Reference Vault Chunks ───────────────────────────────────────────────

def save_reference_vault_chunk(
    chunk_id: str = None,
    document_id: str = None,
    user_id: str = None,
    project_id: str = None,
    parent_chunk_id: str = None,
    chunk_index: int = None,
    markdown_content: str = None,
    content: str = None,
    content_hash: str = None,
    page_number: int = None,
    section_title: str = None,
    pinecone_vector_id: str = None,
    embedding_model: str = None,
    source_type: str = 'reference_document',
    metadata_json=None,
) -> str:
    """Insert or replace a Reference Vault chunk and return its string ID."""
    if not document_id:
        raise ValueError("document_id is required")
    if not user_id:
        raise ValueError("user_id is required")
    if not project_id:
        project_id = create_default_project(user_id)
    if chunk_index is None:
        raise ValueError("chunk_index is required")

    chunk_id = chunk_id or _new_id()
    now = _utc_now()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT OR REPLACE INTO reference_vault_chunks (
                chunk_id, document_id, user_id, project_id, parent_chunk_id,
                chunk_index, markdown_content, content, content_hash,
                page_number, section_title, pinecone_vector_id,
                embedding_model, source_type, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                chunk_id, document_id, user_id, project_id, parent_chunk_id,
                chunk_index, markdown_content, content, content_hash,
                page_number, section_title, pinecone_vector_id, embedding_model,
                source_type, _json_text(metadata_json), now,
            )
        )
        conn.commit()
    return chunk_id


def save_reference_vault_chunks_batch(records: list):
    """Save multiple Reference Vault chunks in a single transaction."""
    if not records:
        return

    now = _utc_now()
    prepared = []
    for record in records:
        user_id = record.get('user_id')
        if not user_id:
            raise ValueError("user_id is required for every chunk")
        project_id = record.get('project_id') or create_default_project(user_id)
        chunk_index = record.get('chunk_index')
        if chunk_index is None:
            raise ValueError("chunk_index is required for every chunk")
        prepared.append((
            record.get('chunk_id') or _new_id(),
            record.get('document_id'),
            user_id,
            project_id,
            record.get('parent_chunk_id'),
            chunk_index,
            record.get('markdown_content'),
            record.get('content'),
            record.get('content_hash'),
            record.get('page_number'),
            record.get('section_title'),
            record.get('pinecone_vector_id'),
            record.get('embedding_model'),
            record.get('source_type', 'reference_document'),
            _json_text(record.get('metadata_json')),
            record.get('created_at') or now,
        ))
    if any(row[1] is None for row in prepared):
        raise ValueError("document_id is required for every chunk")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            '''
            INSERT OR REPLACE INTO reference_vault_chunks (
                chunk_id, document_id, user_id, project_id, parent_chunk_id,
                chunk_index, markdown_content, content, content_hash,
                page_number, section_title, pinecone_vector_id,
                embedding_model, source_type, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            prepared
        )
        conn.commit()


def get_reference_vault_chunk(chunk_id: str) -> dict | None:
    """Retrieve a Reference Vault chunk by ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM reference_vault_chunks WHERE chunk_id = ?',
            (chunk_id,)
        )
        return _row_to_dict(cursor, cursor.fetchone())


def get_reference_vault_chunks_by_document(document_id: str) -> list:
    """Retrieve Reference Vault chunks for a document ordered by chunk index."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM reference_vault_chunks WHERE document_id = ? '
            'ORDER BY chunk_index ASC',
            (document_id,)
        )
        return [_row_to_dict(cursor, row) for row in cursor.fetchall()]


def get_reference_vault_chunks_by_vector_ids(
    vector_ids: list,
    user_id: str,
    project_id: str = None,
) -> list:
    """Retrieve Reference Vault chunks by Pinecone vector IDs with user scope."""
    if not vector_ids:
        return []
    placeholders = ','.join('?' for _ in vector_ids)
    query = (
        f'SELECT * FROM reference_vault_chunks WHERE pinecone_vector_id IN ({placeholders}) '
        'AND user_id = ?'
    )
    params = list(vector_ids) + [user_id]
    if project_id is not None:
        query += ' AND project_id = ?'
        params.append(project_id)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [_row_to_dict(cursor, row) for row in cursor.fetchall()]


def update_reference_vault_chunk_vector_id(
    chunk_id: str,
    pinecone_vector_id: str,
) -> bool:
    """Persist the Pinecone vector ID for a Reference Vault chunk."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE reference_vault_chunks SET pinecone_vector_id = ? WHERE chunk_id = ?',
            (pinecone_vector_id, chunk_id)
        )
        conn.commit()
        return cursor.rowcount > 0


# ── v3 OpenAlex Cache and Citation Logging ──────────────────────────────────

def cache_openalex_search(query: str, response_json, user_id: str = None) -> str:
    """Cache an OpenAlex search response as SQLite TEXT."""
    cache_id = _new_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO openalex_search_cache '
            '(cache_id, user_id, query, response_json, created_at) VALUES (?, ?, ?, ?, ?)',
            (cache_id, user_id, query, _json_text(response_json), _utc_now())
        )
        conn.commit()
    return cache_id


def get_openalex_search_cache(query: str, user_id: str = None) -> dict | None:
    """Return the newest cached OpenAlex response for a query."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if user_id is None:
            cursor.execute(
                'SELECT * FROM openalex_search_cache WHERE query = ? AND user_id IS NULL '
                'ORDER BY created_at DESC LIMIT 1',
                (query,)
            )
        else:
            cursor.execute(
                'SELECT * FROM openalex_search_cache WHERE query = ? AND user_id = ? '
                'ORDER BY created_at DESC LIMIT 1',
                (query, user_id)
            )
        return _row_to_dict(cursor, cursor.fetchone())


def save_citation_candidate(
    candidate_id: str = None,
    user_id: str = None,
    project_id: str = None,
    document_id: str = None,
    chunk_id: str = None,
    author_display: str = None,
    paper_name: str = None,
    evidence_excerpt: str = None,
    metadata_json=None,
) -> str:
    """Save a verified citation candidate for later Workbench citation phases."""
    if not user_id:
        raise ValueError("user_id is required")
    if not project_id:
        project_id = create_default_project(user_id)
    if not document_id:
        raise ValueError("document_id is required")

    candidate_id = candidate_id or _new_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT OR REPLACE INTO citation_candidates (
                candidate_id, user_id, project_id, document_id, chunk_id,
                author_display, paper_name, evidence_excerpt, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                candidate_id, user_id, project_id, document_id, chunk_id,
                author_display, paper_name, evidence_excerpt,
                _json_text(metadata_json), _utc_now(),
            )
        )
        conn.commit()
    return candidate_id


def save_citation_log(
    citation_log_id: str = None,
    user_id: str = None,
    project_id: str = None,
    workbench_document_id: str = None,
    document_id: str = None,
    chunk_id: str = None,
    citation_text: str = None,
    citation_format: str = '[author_name, paper_name]',
    metadata_json=None,
) -> str:
    """Save a citation rendering log entry."""
    if not user_id:
        raise ValueError("user_id is required")
    if not project_id:
        project_id = create_default_project(user_id)

    citation_log_id = citation_log_id or _new_id()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT OR REPLACE INTO citation_logs (
                citation_log_id, user_id, project_id, workbench_document_id,
                document_id, chunk_id, citation_text, citation_format,
                metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                citation_log_id, user_id, project_id, workbench_document_id,
                document_id, chunk_id, citation_text, citation_format,
                _json_text(metadata_json), _utc_now(),
            )
        )
        conn.commit()
    return citation_log_id


# ── Research Notes ────────────────────────────────────────────────────────────

def save_note(title: str, content: str, user_id: str = None) -> int:
    """Save a research note. Returns the new note ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO research_notes (title, content, user_id) VALUES (?, ?, ?)',
            (title, content, user_id)
        )
        conn.commit()
        return cursor.lastrowid


def load_all_notes(user_id: str = None) -> list:
    """Load research notes for a specific user ordered newest-first."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute(
                'SELECT id, title, content, timestamp FROM research_notes '
                'WHERE user_id = ? ORDER BY timestamp DESC',
                (user_id,)
            )
        else:
            cursor.execute(
                'SELECT id, title, content, timestamp FROM research_notes ORDER BY timestamp DESC'
            )
        return [
            {'id': row[0], 'title': row[1], 'content': row[2], 'timestamp': row[3]}
            for row in cursor.fetchall()
        ]


def delete_note_by_id(note_id: int, user_id: str = None) -> bool:
    """Delete a research note by ID. If user_id provided, scopes to that user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute('DELETE FROM research_notes WHERE id = ? AND user_id = ?',
                           (note_id, user_id))
        else:
            cursor.execute('DELETE FROM research_notes WHERE id = ?', (note_id,))
        conn.commit()
        return cursor.rowcount > 0


# ── Document Metadata ─────────────────────────────────────────────────────────

def save_document_metadata(filename: str, file_type: str,
                            chunk_count: int, db_path: str,
                            user_id: str = None) -> int:
    """Save uploaded document metadata. Returns the new document ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO documents (filename, file_type, chunk_count, db_path, user_id) '
            'VALUES (?, ?, ?, ?, ?)',
            (filename, file_type, chunk_count, db_path, user_id)
        )
        conn.commit()
        return cursor.lastrowid


def load_all_documents(user_id: str = None) -> list:
    """Load document metadata for a specific user ordered newest-first."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute(
                'SELECT id, filename, file_type, chunk_count, db_path, timestamp '
                'FROM documents WHERE user_id = ? ORDER BY timestamp DESC',
                (user_id,)
            )
        else:
            cursor.execute(
                'SELECT id, filename, file_type, chunk_count, db_path, timestamp '
                'FROM documents ORDER BY timestamp DESC'
            )
        return [
            {
                'id': row[0], 'filename': row[1], 'file_type': row[2],
                'chunk_count': row[3], 'db_path': row[4], 'timestamp': row[5]
            }
            for row in cursor.fetchall()
        ]


def delete_document_by_id(doc_id: int, user_id: str = None) -> bool:
    """Delete a document metadata record by ID. If user_id provided, scopes to that user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute('DELETE FROM documents WHERE id = ? AND user_id = ?',
                           (doc_id, user_id))
        else:
            cursor.execute('DELETE FROM documents WHERE id = ?', (doc_id,))
        conn.commit()
        return cursor.rowcount > 0


# ── Parent Chunks (Advanced RAG) ─────────────────────────────────────────────

def save_parent_chunk(parent_id: str, content: str, source_file: str = None,
                      page_number: int = None, section: str = None):
    """Save a parent chunk. Uses INSERT OR REPLACE to allow updates."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO parent_chunks (id, content, source_file, page_number, section) '
            'VALUES (?, ?, ?, ?, ?)',
            (parent_id, content, source_file, page_number, section)
        )
        conn.commit()


def save_parent_chunks_batch(records: list):
    """Save multiple parent chunks in a single transaction.

    Args:
        records: list of dicts with keys: id, content, source_file, page_number, section
    """
    if not records:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            'INSERT OR REPLACE INTO parent_chunks (id, content, source_file, page_number, section) '
            'VALUES (?, ?, ?, ?, ?)',
            [(r['id'], r['content'], r.get('source_file'), r.get('page_number'), r.get('section'))
             for r in records]
        )
        conn.commit()


def get_parent_chunk(parent_id: str) -> dict | None:
    """Retrieve a parent chunk by ID. Returns dict or None."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, content, source_file, page_number, section FROM parent_chunks WHERE id = ?',
            (parent_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0], 'content': row[1], 'source_file': row[2],
                'page_number': row[3], 'section': row[4]
            }
        return None


def get_parent_chunks_batch(parent_ids: list) -> dict:
    """Retrieve multiple parent chunks by IDs. Returns {id: dict}."""
    if not parent_ids:
        return {}
    with get_db_connection() as conn:
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in parent_ids)
        cursor.execute(
            f'SELECT id, content, source_file, page_number, section '
            f'FROM parent_chunks WHERE id IN ({placeholders})',
            parent_ids
        )
        return {
            row[0]: {
                'id': row[0], 'content': row[1], 'source_file': row[2],
                'page_number': row[3], 'section': row[4]
            }
            for row in cursor.fetchall()
        }


def delete_parent_chunks_by_source(source_file: str) -> int:
    """Delete all parent chunks for a given source file. Returns count deleted."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM parent_chunks WHERE source_file = ?', (source_file,))
        conn.commit()
        return cursor.rowcount


# ── Web Pages ──────────────────────────────────────────────────────────────────

def save_web_page(url: str, title: str, summary: str, chunk_count: int = 0,
                  user_id: str = None) -> int:
    """Save web page metadata. Returns new web_page ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO web_pages (url, title, summary, chunk_count, user_id) '
            'VALUES (?, ?, ?, ?, ?)',
            (url, title, summary, chunk_count, user_id)
        )
        conn.commit()
        return cursor.lastrowid


def load_all_web_pages(user_id: str = None) -> list:
    """Load web pages for a specific user, ordered newest-first."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute(
                'SELECT id, url, title, summary, chunk_count, timestamp '
                'FROM web_pages WHERE user_id = ? ORDER BY timestamp DESC',
                (user_id,)
            )
        else:
            cursor.execute(
                'SELECT id, url, title, summary, chunk_count, timestamp '
                'FROM web_pages ORDER BY timestamp DESC'
            )
        return [
            {
                'id': row[0], 'url': row[1], 'title': row[2],
                'summary': row[3], 'chunk_count': row[4], 'timestamp': row[5]
            }
            for row in cursor.fetchall()
        ]


def delete_web_page_by_id(page_id: int, user_id: str = None) -> bool:
    """Delete a web page by ID. If user_id provided, scopes to that user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute('DELETE FROM web_pages WHERE id = ? AND user_id = ?',
                           (page_id, user_id))
        else:
            cursor.execute('DELETE FROM web_pages WHERE id = ?', (page_id,))
        conn.commit()
        return cursor.rowcount > 0


def update_web_page_title(page_id: int, new_title: str) -> bool:
    """Update the title of a web page."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE web_pages SET title = ? WHERE id = ?',
            (new_title, page_id)
        )
        conn.commit()
        return cursor.rowcount > 0


def update_web_page(page_id: int, new_title: str, new_summary: str,
                    chunk_count: int = None) -> bool:
    """Update title, summary (and optionally chunk_count) of a web page."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if chunk_count is not None:
            cursor.execute(
                'UPDATE web_pages SET title = ?, summary = ?, chunk_count = ? WHERE id = ?',
                (new_title, new_summary, chunk_count, page_id)
            )
        else:
            cursor.execute(
                'UPDATE web_pages SET title = ?, summary = ? WHERE id = ?',
                (new_title, new_summary, page_id)
            )
        conn.commit()
        return cursor.rowcount > 0


def get_web_page_by_id(page_id: int) -> dict | None:
    """Retrieve a web page by ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, url, title, summary, chunk_count, timestamp '
            'FROM web_pages WHERE id = ?',
            (page_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0], 'url': row[1], 'title': row[2],
                'summary': row[3], 'chunk_count': row[4], 'timestamp': row[5]
            }
        return None


# ── Users (Google OAuth) ──────────────────────────────────────────────────────

def save_user(user_info: dict) -> None:
    """
    Save or update a Google OAuth user.
    Uses INSERT OR REPLACE to upsert on the primary key (id).

    Args:
        user_info: dict with keys: id, email, name, picture
    """
    from datetime import datetime
    now = datetime.now().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Check if user already exists to preserve created_at
        cursor.execute('SELECT created_at FROM users WHERE id = ?', (user_info['id'],))
        row = cursor.fetchone()
        created_at = row[0] if row else now

        cursor.execute(
            'INSERT OR REPLACE INTO users (id, email, name, picture, created_at, last_login) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (
                user_info['id'],
                user_info['email'],
                user_info.get('name', ''),
                user_info.get('picture', ''),
                created_at,
                now,
            )
        )
        conn.commit()


def get_user(user_id: str) -> dict | None:
    """Retrieve a user by ID. Returns dict or None."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, email, name, picture, created_at, last_login '
            'FROM users WHERE id = ?',
            (user_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                'id': row[0], 'email': row[1], 'name': row[2],
                'picture': row[3], 'created_at': row[4], 'last_login': row[5],
            }
        return None


def get_total_users() -> int:
    """Return the total number of registered users."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users')
        row = cursor.fetchone()
        return row[0] if row else 0


# ── Editor Documents (per-user file storage in SQLite) ───────────────────────

def initialize_editor_documents_table():
    """Create the editor_documents table for per-user editor file persistence."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS editor_documents (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                name        TEXT NOT NULL,
                title       TEXT NOT NULL,
                content     TEXT NOT NULL,
                timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name)
            )
        ''')
        conn.commit()


def save_editor_document(user_id: str, name: str, title: str, content: str) -> int:
    """
    Save or overwrite an editor document for a user.
    Uses INSERT OR REPLACE so Save (overwrite) and Save As (new name) both work.
    Returns the document ID.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO editor_documents (user_id, name, title, content, timestamp) '
            'VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)',
            (user_id, name, title, content)
        )
        conn.commit()
        return cursor.lastrowid


def load_editor_document(user_id: str, name: str) -> dict | None:
    """Load a specific editor document by user_id and name. Returns dict or None."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, name, title, content, timestamp FROM editor_documents '
            'WHERE user_id = ? AND name = ?',
            (user_id, name)
        )
        row = cursor.fetchone()
        if row:
            return {'id': row[0], 'name': row[1], 'title': row[2],
                    'content': row[3], 'timestamp': row[4]}
        return None


def list_editor_documents(user_id: str) -> list:
    """List all editor documents for a user, ordered newest-first."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, name, title, content, timestamp FROM editor_documents '
            'WHERE user_id = ? ORDER BY timestamp DESC',
            (user_id,)
        )
        return [
            {'id': row[0], 'name': row[1], 'title': row[2],
             'content': row[3], 'timestamp': row[4]}
            for row in cursor.fetchall()
        ]


def delete_editor_document(user_id: str, name: str) -> bool:
    """Delete an editor document by user_id and name. Returns True if deleted."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'DELETE FROM editor_documents WHERE user_id = ? AND name = ?',
            (user_id, name)
        )
        conn.commit()
        return cursor.rowcount > 0


# ── Token Usage ───────────────────────────────────────────────────────────────

def record_token_usage(user_id: str, input_tokens: int, output_tokens: int,
                       function_name: str = "") -> int:
    """Record a token usage event. Returns the new row ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO token_usage (user_id, input_tokens, output_tokens, function_name) '
            'VALUES (?, ?, ?, ?)',
            (user_id, input_tokens, output_tokens, function_name)
        )
        conn.commit()
        return cursor.lastrowid


def get_total_token_usage(user_id: str = None) -> dict:
    """Return total input/output tokens for a user (or all users if None)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if user_id is not None:
            cursor.execute(
                'SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) '
                'FROM token_usage WHERE user_id = ?',
                (user_id,)
            )
        else:
            cursor.execute(
                'SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) '
                'FROM token_usage'
            )
        row = cursor.fetchone()
        return {'input_tokens': row[0], 'output_tokens': row[1]}


# Initialize database on import (idempotent — CREATE TABLE IF NOT EXISTS)
initialize_database()
initialize_editor_documents_table()
