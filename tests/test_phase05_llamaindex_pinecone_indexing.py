import shutil
import uuid
from pathlib import Path

import pytest

import database
import llamaindex_pinecone_rag as rag


class FakeNode:
    def __init__(self, text, metadata=None):
        self.text = text
        self.metadata = metadata or {}
        self.id_ = None

    def get_content(self, metadata_mode=None):
        return self.text


class FakeCandidate:
    def __init__(self, node):
        self.node = node


@pytest.fixture
def tmp_path():
    path = Path("tests") / ".tmp" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _setup_temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "research_notes.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.initialize_database()
    return db_path


def _active_doc(tmp_path, user_id="user-1", project_id=None, status="active"):
    project_id = project_id or database.create_default_project(user_id)
    pdf_path = tmp_path / "paper.pdf"
    md_path = tmp_path / "paper.md"
    pdf_path.write_bytes(b"%PDF-1.4 test")
    md_path.write_text("# Paper\n\nSQLite verified content.", encoding="utf-8")
    document_id = database.save_reference_vault_document(
        user_id=user_id,
        project_id=project_id,
        paper_name="Paper Title",
        author_display="Author Name",
        filename="paper.pdf",
        file_type="pdf",
        storage_path=str(pdf_path),
        docling_markdown_path=str(md_path),
        docling_status="succeeded",
        source_type="reference_document",
        status=status,
        metadata_json={"index_status": "pending"},
    )
    return document_id, project_id


def test_index_reference_vault_document_creates_chunks_and_vector_ids(tmp_path, monkeypatch):
    _setup_temp_db(tmp_path, monkeypatch)
    document_id, project_id = _active_doc(tmp_path)

    monkeypatch.setattr(
        rag,
        "_build_nodes_from_markdown",
        lambda document: [FakeNode("First chunk"), FakeNode("Second chunk")],
    )
    monkeypatch.setattr(rag, "_delete_existing_vectors", lambda document: None)
    monkeypatch.setattr(
        rag,
        "_index_nodes_with_llamaindex",
        lambda nodes, user_id: [node.id_ for node in nodes],
    )

    result = rag.index_reference_vault_document(document_id, "user-1", project_id)

    assert result["index_status"] == "indexed"
    assert result["chunk_count"] == 2
    chunks = database.get_reference_vault_chunks_by_document(
        document_id,
        user_id="user-1",
        project_id=project_id,
    )
    assert len(chunks) == 2
    assert all(chunk["source_type"] == "reference_document" for chunk in chunks)
    assert all(chunk["pinecone_vector_id"] == chunk["chunk_id"] for chunk in chunks)


def test_index_nodes_include_required_pinecone_metadata(tmp_path, monkeypatch):
    _setup_temp_db(tmp_path, monkeypatch)
    document_id, project_id = _active_doc(tmp_path)
    indexed_nodes = []

    monkeypatch.setattr(
        rag,
        "_build_nodes_from_markdown",
        lambda document: [FakeNode("Metadata chunk")],
    )
    monkeypatch.setattr(rag, "_delete_existing_vectors", lambda document: None)

    def capture_index(nodes, user_id):
        indexed_nodes.extend(nodes)
        return [node.id_ for node in nodes]

    monkeypatch.setattr(rag, "_index_nodes_with_llamaindex", capture_index)

    rag.index_reference_vault_document(document_id, "user-1", project_id)

    metadata = indexed_nodes[0].metadata
    assert metadata["user_id"] == "user-1"
    assert metadata["project_id"] == project_id
    assert metadata["document_id"] == document_id
    assert metadata["chunk_id"] == indexed_nodes[0].id_
    assert metadata["source_type"] == "reference_document"


def test_index_rejects_metadata_only_document(tmp_path, monkeypatch):
    _setup_temp_db(tmp_path, monkeypatch)
    document_id, project_id = _active_doc(tmp_path, status="metadata_only")

    try:
        rag.index_reference_vault_document(document_id, "user-1", project_id)
    except ValueError as exc:
        assert "active" in str(exc)
    else:
        raise AssertionError("expected metadata_only document rejection")


def test_retrieve_reference_vault_verifies_sqlite_and_uses_sqlite_content(tmp_path, monkeypatch):
    _setup_temp_db(tmp_path, monkeypatch)
    document_id, project_id = _active_doc(tmp_path)
    chunk_id = database.save_reference_vault_chunk(
        chunk_id="chunk-1",
        document_id=document_id,
        user_id="user-1",
        project_id=project_id,
        chunk_index=0,
        content="SQLite verified content",
        pinecone_vector_id="chunk-1",
        source_type="reference_document",
    )
    candidate_node = FakeNode(
        "Untrusted Pinecone content",
        metadata={
            "chunk_id": chunk_id,
            "source_type": "reference_document",
            "user_id": "user-1",
            "project_id": project_id,
        },
    )
    candidate_node.id_ = chunk_id
    monkeypatch.setattr(
        rag,
        "_retrieve_nodes_with_llamaindex",
        lambda query, user_id, project_id, k: [FakeCandidate(candidate_node)],
    )

    docs = rag.retrieve_reference_vault("query", "user-1", project_id=project_id)

    assert len(docs) == 1
    assert docs[0].page_content == "SQLite verified content"
    assert docs[0].metadata["document_id"] == document_id


def test_retrieve_reference_vault_does_not_broaden_filters(tmp_path, monkeypatch):
    _setup_temp_db(tmp_path, monkeypatch)
    calls = {"count": 0}

    def empty_retrieve(query, user_id, project_id, k):
        calls["count"] += 1
        return []

    monkeypatch.setattr(rag, "_retrieve_nodes_with_llamaindex", empty_retrieve)

    assert rag.retrieve_reference_vault("query", "user-1") == []
    assert calls["count"] == 1


def test_retrieve_reference_vault_rejects_metadata_only_document(tmp_path, monkeypatch):
    _setup_temp_db(tmp_path, monkeypatch)
    document_id, project_id = _active_doc(tmp_path, status="metadata_only")
    database.save_reference_vault_chunk(
        chunk_id="chunk-1",
        document_id=document_id,
        user_id="user-1",
        project_id=project_id,
        chunk_index=0,
        content="Should not return",
        pinecone_vector_id="chunk-1",
        source_type="reference_document",
    )
    candidate_node = FakeNode("Pinecone content", metadata={"chunk_id": "chunk-1"})
    candidate_node.id_ = "chunk-1"
    monkeypatch.setattr(
        rag,
        "_retrieve_nodes_with_llamaindex",
        lambda query, user_id, project_id, k: [FakeCandidate(candidate_node)],
    )

    assert rag.retrieve_reference_vault("query", "user-1", project_id=project_id) == []


def test_index_rejects_note_source_type(tmp_path, monkeypatch):
    _setup_temp_db(tmp_path, monkeypatch)
    project_id = database.create_default_project("user-1")
    pdf_path = tmp_path / "paper.pdf"
    md_path = tmp_path / "paper.md"
    pdf_path.write_bytes(b"%PDF-1.4 test")
    md_path.write_text("text", encoding="utf-8")
    document_id = database.save_reference_vault_document(
        user_id="user-1",
        project_id=project_id,
        filename="paper.pdf",
        storage_path=str(pdf_path),
        docling_markdown_path=str(md_path),
        docling_status="succeeded",
        source_type="note",
        status="active",
    )

    try:
        rag.index_reference_vault_document(document_id, "user-1", project_id)
    except ValueError as exc:
        assert "Reference Vault documents" in str(exc)
    else:
        raise AssertionError("expected non-Reference Vault rejection")
