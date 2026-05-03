import sqlite3
import shutil
import uuid
from pathlib import Path

import database


def _workspace_tmp():
    path = Path("tests") / ".tmp" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_reference_vault_schema_and_helpers_use_temp_sqlite(monkeypatch):
    tmp_path = _workspace_tmp()
    db_path = tmp_path / "research_notes.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)

    try:
        database.initialize_database()

        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            tables = {row[0] for row in cursor.fetchall()}

        assert {
            "projects",
            "reference_vault_documents",
            "reference_vault_chunks",
            "openalex_search_cache",
            "citation_candidates",
            "citation_logs",
        }.issubset(tables)
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(reference_vault_documents)")
            document_columns = {row[1] for row in cursor.fetchall()}
            cursor.execute("PRAGMA table_info(reference_vault_chunks)")
            chunk_columns = {row[1] for row in cursor.fetchall()}

        assert {
            "source_origin",
            "source_name",
            "landing_page_url",
            "pdf_url",
            "summary",
        }.issubset(document_columns)
        assert "node_metadata_json" in chunk_columns

        project_id = database.create_default_project("user-1")
        assert database.get_default_project_id("user-1") == project_id
        other_project_id = database.create_default_project("user-2")

        document_id = database.save_reference_vault_document(
            user_id="user-1",
            project_id=project_id,
            paper_name="Paper Title",
            author_display="Author Name",
            source_origin="upload",
            source_name="paper.pdf",
            landing_page_url="https://example.test/work",
            pdf_url="https://example.test/work.pdf",
            summary="Short summary",
            metadata_json={"source": "test"},
        )
        chunk_id = database.save_reference_vault_chunk(
            document_id=document_id,
            user_id="user-1",
            project_id=project_id,
            chunk_index=0,
            content="chunk content",
            pinecone_vector_id="vector-1",
            node_metadata_json={"node": "metadata"},
        )
        other_document_id = database.save_reference_vault_document(
            user_id="user-2",
            project_id=other_project_id,
            paper_name="Other Paper",
        )
        database.save_reference_vault_chunk(
            chunk_id="other-chunk",
            document_id=other_document_id,
            user_id="user-2",
            project_id=other_project_id,
            chunk_index=0,
            content="other content",
            pinecone_vector_id="other-vector",
        )

        document = database.get_reference_vault_document(document_id, user_id="user-1")
        assert document["paper_name"] == "Paper Title"
        assert document["source_type"] == "reference_document"
        assert document["source_origin"] == "upload"
        assert document["source_name"] == "paper.pdf"
        assert document["landing_page_url"] == "https://example.test/work"
        assert document["pdf_url"] == "https://example.test/work.pdf"
        assert document["summary"] == "Short summary"
        assert database.get_reference_vault_document(document_id, user_id="user-2") is None

        chunks = database.get_reference_vault_chunks_by_vector_ids(
            ["vector-1"],
            user_id="user-1",
            project_id=project_id,
        )
        assert [chunk["chunk_id"] for chunk in chunks] == [chunk_id]
        assert database.get_reference_vault_chunks_by_vector_ids(
            ["vector-1"],
            user_id="user-2",
            project_id=other_project_id,
        ) == []

        scoped_chunks = database.get_reference_vault_chunks_by_document(
            document_id,
            user_id="user-1",
            project_id=project_id,
        )
        assert [chunk["chunk_id"] for chunk in scoped_chunks] == [chunk_id]
        assert database.get_reference_vault_chunks_by_document(
            document_id,
            user_id="user-2",
            project_id=other_project_id,
        ) == []

        assert not database.update_reference_vault_document_metadata(
            document_id,
            metadata_json={"blocked": True},
            user_id="user-2",
            project_id=other_project_id,
        )
        assert database.update_reference_vault_document_metadata(
            document_id,
            metadata_json={"allowed": True},
            user_id="user-1",
            project_id=project_id,
        )
        updated_document = database.get_reference_vault_document(document_id, user_id="user-1")
        assert '"allowed": true' in updated_document["metadata_json"]

        assert not database.update_reference_vault_chunk_vector_id(
            chunk_id,
            "blocked-vector",
            user_id="user-2",
            project_id=other_project_id,
        )
        assert database.update_reference_vault_chunk_vector_id(
            chunk_id,
            "updated-vector",
            user_id="user-1",
            project_id=project_id,
        )
        assert database.get_reference_vault_chunks_by_vector_ids(
            ["updated-vector"],
            user_id="user-1",
            project_id=project_id,
        )[0]["chunk_id"] == chunk_id
        assert database.delete_reference_vault_chunks_by_document(
            document_id,
            user_id="user-2",
            project_id=other_project_id,
        ) == 0

        assert database.delete_reference_vault_document(document_id, user_id="user-1")
        archived = database.get_reference_vault_document(document_id, user_id="user-1")
        assert archived["status"] == "archived"
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
