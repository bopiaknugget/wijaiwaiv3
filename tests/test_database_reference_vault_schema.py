import sqlite3

import database


def test_reference_vault_schema_and_helpers_use_temp_sqlite(tmp_path, monkeypatch):
    db_path = tmp_path / "research_notes.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)

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

    project_id = database.create_default_project("user-1")
    assert database.get_default_project_id("user-1") == project_id

    document_id = database.save_reference_vault_document(
        user_id="user-1",
        project_id=project_id,
        paper_name="Paper Title",
        author_display="Author Name",
        metadata_json={"source": "test"},
    )
    chunk_id = database.save_reference_vault_chunk(
        document_id=document_id,
        user_id="user-1",
        project_id=project_id,
        chunk_index=0,
        content="chunk content",
        pinecone_vector_id="vector-1",
    )

    document = database.get_reference_vault_document(document_id, user_id="user-1")
    assert document["paper_name"] == "Paper Title"
    assert document["source_type"] == "reference_document"

    chunks = database.get_reference_vault_chunks_by_vector_ids(
        ["vector-1"],
        user_id="user-1",
        project_id=project_id,
    )
    assert [chunk["chunk_id"] for chunk in chunks] == [chunk_id]

    assert database.delete_reference_vault_document(document_id, user_id="user-1")
    archived = database.get_reference_vault_document(document_id, user_id="user-1")
    assert archived["status"] == "archived"
