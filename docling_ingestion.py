"""
Docling ingestion for Reference Vault uploaded papers.

Converts an uploaded PDF to Markdown, stores the required artifacts, and keeps
the Reference Vault SQLite row in sync with ingestion status.
"""

from __future__ import annotations

import json
import re
import shutil
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import database
from tls_config import sanitize_tls_ca_bundle_env


REFERENCE_VAULT_STORAGE_ROOT = Path(__file__).parent / "storage" / "reference_vault"


@dataclass(frozen=True)
class DoclingIngestionResult:
    document_id: str
    user_id: str
    project_id: str
    filename: str
    storage_dir: Path
    original_pdf_path: Path
    markdown_path: Path
    metadata_path: Path
    markdown_text: str
    metadata: dict[str, Any]


def _safe_path_segment(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return "unknown"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _utc_now() -> str:
    return datetime.utcnow().isoformat()


def _convert_pdf_to_markdown(pdf_path: Path) -> tuple[str, dict[str, Any]]:
    sanitize_tls_ca_bundle_env()
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise RuntimeError(
            "Docling is not installed. Install dependencies from requirements.txt."
        ) from exc

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    document = getattr(result, "document", None)
    if document is None or not hasattr(document, "export_to_markdown"):
        raise RuntimeError("Docling conversion did not return an exportable document.")

    markdown_text = document.export_to_markdown()
    if not markdown_text or not markdown_text.strip():
        raise RuntimeError("Docling conversion produced empty Markdown.")

    metadata = {
        "converter": "docling",
        "converted_at": _utc_now(),
        "source_pdf": str(pdf_path),
    }
    status = getattr(result, "status", None)
    if status is not None:
        metadata["docling_result_status"] = str(status)
    return markdown_text, metadata


def ingest_uploaded_pdf_with_docling(
    pdf_path: str | Path,
    filename: str,
    user_id: str,
    project_id: str | None = None,
    document_id: str | None = None,
) -> DoclingIngestionResult:
    """
    Store an uploaded PDF and convert it to Markdown with Docling.

    Status handling:
    - processing: record created before conversion
    - active: original PDF, Markdown, and metadata artifacts written
    - failed: conversion/artifact write failed; caller must not index chunks
    """
    if not user_id:
        raise ValueError("user_id is required")

    project_id = project_id or database.create_default_project(user_id)
    source_pdf_path = Path(pdf_path)
    if not source_pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {source_pdf_path}")

    document_id = database.save_reference_vault_document(
        document_id=document_id,
        user_id=user_id,
        project_id=project_id,
        filename=filename,
        file_type="pdf",
        source_type="reference_document",
        status="processing",
        docling_status="processing",
        metadata_json={"ingestion_stage": "docling_conversion"},
    )

    storage_dir = (
        REFERENCE_VAULT_STORAGE_ROOT
        / _safe_path_segment(user_id)
        / _safe_path_segment(project_id)
        / _safe_path_segment(document_id)
    )
    original_pdf_path = storage_dir / "original.pdf"
    markdown_path = storage_dir / "docling.md"
    metadata_path = storage_dir / "docling_meta.json"

    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_pdf_path, original_pdf_path)
        markdown_text, metadata = _convert_pdf_to_markdown(original_pdf_path)
        metadata.update({
            "document_id": document_id,
            "user_id": user_id,
            "project_id": project_id,
            "filename": filename,
            "index_status": "pending",
            "original_pdf_path": str(original_pdf_path),
            "docling_markdown_path": str(markdown_path),
            "docling_metadata_path": str(metadata_path),
        })

        markdown_path.write_text(markdown_text, encoding="utf-8")
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        database.save_reference_vault_document(
            document_id=document_id,
            user_id=user_id,
            project_id=project_id,
            filename=filename,
            file_type="pdf",
            storage_path=str(original_pdf_path),
            docling_markdown_path=str(markdown_path),
            docling_status="succeeded",
            source_type="reference_document",
            status="active",
            metadata_json={
                "ingestion_stage": "docling_complete",
                "index_status": "pending",
                "docling_metadata_path": str(metadata_path),
            },
            extraction_metadata_json=metadata,
        )

        return DoclingIngestionResult(
            document_id=document_id,
            user_id=user_id,
            project_id=project_id,
            filename=filename,
            storage_dir=storage_dir,
            original_pdf_path=original_pdf_path,
            markdown_path=markdown_path,
            metadata_path=metadata_path,
            markdown_text=markdown_text,
            metadata=metadata,
        )
    except Exception as exc:
        error_metadata = {
            "ingestion_stage": "docling_failed",
            "error": str(exc),
            "traceback": traceback.format_exc(limit=5),
            "original_pdf_path": str(original_pdf_path),
            "docling_markdown_path": str(markdown_path),
            "docling_metadata_path": str(metadata_path),
        }
        database.save_reference_vault_document(
            document_id=document_id,
            user_id=user_id,
            project_id=project_id,
            filename=filename,
            file_type="pdf",
            storage_path=str(original_pdf_path) if original_pdf_path.exists() else None,
            docling_markdown_path=str(markdown_path) if markdown_path.exists() else None,
            docling_status="failed",
            source_type="reference_document",
            status="failed",
            metadata_json=error_metadata,
            extraction_metadata_json=error_metadata,
        )
        raise
