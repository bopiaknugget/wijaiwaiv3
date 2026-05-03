import sqlite3
import shutil
import uuid
from pathlib import Path

import database
import docling_ingestion


def _workspace_tmp():
    path = Path("tests") / ".tmp" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def _setup_temp_db(tmp_path, monkeypatch):
    db_path = tmp_path / "research_notes.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.initialize_database()
    return db_path


def test_docling_ingestion_stores_artifacts_and_active_status(monkeypatch):
    tmp_path = _workspace_tmp()
    try:
        _setup_temp_db(tmp_path, monkeypatch)
        monkeypatch.setattr(
            docling_ingestion,
            "REFERENCE_VAULT_STORAGE_ROOT",
            tmp_path / "storage" / "reference_vault",
        )
        monkeypatch.setattr(
            docling_ingestion,
            "_convert_pdf_to_markdown",
            lambda pdf_path: ("# Paper\n\nConverted text.", {"converter": "test"}),
        )

        source_pdf = tmp_path / "upload.pdf"
        source_pdf.write_bytes(b"%PDF-1.4 test")

        result = docling_ingestion.ingest_uploaded_pdf_with_docling(
            source_pdf,
            filename="paper.pdf",
            user_id="user-1",
        )

        assert result.original_pdf_path.read_bytes() == b"%PDF-1.4 test"
        assert result.markdown_path.read_text(encoding="utf-8") == "# Paper\n\nConverted text."
        assert result.metadata_path.exists()

        document = database.get_reference_vault_document(
            result.document_id,
            user_id="user-1",
            project_id=result.project_id,
        )
        assert document["status"] == "active"
        assert document["docling_status"] == "succeeded"
        assert document["storage_path"] == str(result.original_pdf_path)
        assert document["docling_markdown_path"] == str(result.markdown_path)
        assert '"index_status": "pending"' in document["metadata_json"]
        assert '"index_status": "pending"' in document["extraction_metadata_json"]
        assert database.get_reference_vault_chunks_by_document(
            result.document_id,
            user_id="user-1",
            project_id=result.project_id,
        ) == []
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_docling_ingestion_failure_marks_document_failed(monkeypatch):
    tmp_path = _workspace_tmp()
    try:
        db_path = _setup_temp_db(tmp_path, monkeypatch)
        monkeypatch.setattr(
            docling_ingestion,
            "REFERENCE_VAULT_STORAGE_ROOT",
            tmp_path / "storage" / "reference_vault",
        )

        def fail_conversion(pdf_path):
            raise RuntimeError("conversion failed")

        monkeypatch.setattr(docling_ingestion, "_convert_pdf_to_markdown", fail_conversion)

        source_pdf = tmp_path / "upload.pdf"
        source_pdf.write_bytes(b"%PDF-1.4 test")

        try:
            docling_ingestion.ingest_uploaded_pdf_with_docling(
                source_pdf,
                filename="paper.pdf",
                user_id="user-1",
            )
        except RuntimeError as exc:
            assert "conversion failed" in str(exc)
        else:
            raise AssertionError("expected conversion failure")

        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, docling_status FROM reference_vault_documents"
            )
            rows = cursor.fetchall()

        assert rows == [("failed", "failed")]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)


def test_phase05_active_upload_calls_reference_vault_indexing_not_legacy_paths():
    app_source = Path("app.py").read_text(encoding="utf-8")
    upload_block = app_source.split('st.markdown("**Upload paper**")', 1)[1]
    upload_block = upload_block.split('st.markdown("**OpenAlex metadata discovery**")', 1)[0]

    assert "ingest_uploaded_pdf_with_docling(" in upload_block
    assert "index_docling_result(" in upload_block
    assert "create_parent_child_chunks(" not in upload_block
    assert "create_summary_documents(" not in upload_block
    assert "save_reference_vault_chunks_batch(" not in upload_block
    assert "ingest_documents(" not in upload_block
    assert "upsert_documents(" not in upload_block
    assert "load_all_documents(" not in app_source
    assert "save_document_metadata(" not in app_source
    assert "delete_document_by_id(" not in app_source
    assert "delete_parent_chunks_by_source(" not in app_source
    assert "legacy document store" not in app_source
