"""
RAG Pipeline — Legacy Phase 1 Reference
----------------------------------------
This file is kept as a standalone reference implementation.
For production use, see main.py (CLI) or app.py (Streamlit UI).

Fixed in this version:
- Replaced deprecated `langchain.document_loaders` with `langchain_community.document_loaders`
- Replaced deprecated `langchain.text_splitter` with `langchain_text_splitters`
- Replaced Google Gemini embeddings with local HuggingFace embeddings (no API key needed)
- Removed deprecated `vector_store.persist()` call (Chroma v0.4+ auto-persists)
"""

import os
import sys

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma


# ============================================================================
# 1. DOCUMENT LOADING
# ============================================================================

def load_pdf_document(pdf_path: str) -> list:
    """
    Load a PDF document using PyPDFLoader.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        list[Document]: One Document per page

    Raises:
        FileNotFoundError: If the file does not exist
        Exception: If loading fails
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(
            f"❌ PDF not found: {pdf_path}\n"
            "Please check the file path and try again."
        )
    try:
        loader = PyPDFLoader(pdf_path)
        documents = loader.load()
        print(f"✓ Loaded PDF: {pdf_path} ({len(documents)} pages)")
        return documents
    except Exception as e:
        raise Exception(f"❌ Error loading PDF: {str(e)}")


# ============================================================================
# 2. TEXT CHUNKING
# ============================================================================

def chunk_documents(documents: list, chunk_size: int = 1000,
                    chunk_overlap: int = 200) -> list:
    """
    Split documents into overlapping chunks using RecursiveCharacterTextSplitter.

    Args:
        documents: List of Document objects
        chunk_size: Maximum characters per chunk (default 1000)
        chunk_overlap: Overlap between consecutive chunks (default 200)

    Returns:
        list[Document]: Chunked documents
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", " ", ""]
    )
    chunks = splitter.split_documents(documents)
    print(f"✓ Chunking complete: {len(chunks)} chunks "
          f"(size={chunk_size}, overlap={chunk_overlap})")
    return chunks


# ============================================================================
# 3. EMBEDDINGS
# ============================================================================

def initialize_embeddings() -> HuggingFaceEmbeddings:
    """
    Initialize a local multilingual HuggingFace embedding model.
    Supports Thai and many other languages. Requires no API key.

    Returns:
        HuggingFaceEmbeddings: Initialized embedding model
    """
    try:
        embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
        print("✓ HuggingFaceEmbeddings initialized")
        return embeddings
    except Exception as e:
        raise ValueError(f"❌ Error initializing embeddings: {str(e)}")


# ============================================================================
# 4. VECTOR STORE
# ============================================================================

def create_vector_store(chunked_documents: list, embeddings,
                        db_path: str = "./Database/chroma_db") -> Chroma:
    """
    Create a ChromaDB vector store from chunked documents.
    Chroma v0.4+ auto-persists to disk — no manual .persist() call needed.

    Args:
        chunked_documents: Pre-chunked Document list
        embeddings: Initialized embedding model
        db_path: Directory path for ChromaDB storage

    Returns:
        Chroma: Populated vector store
    """
    try:
        vector_store = Chroma.from_documents(
            documents=chunked_documents,
            embedding=embeddings,
            persist_directory=db_path,
            collection_name="documents"
        )
        print(f"✓ ChromaDB created at {os.path.abspath(db_path)} "
              f"({len(chunked_documents)} chunks)")
        return vector_store
    except Exception as e:
        raise Exception(f"❌ Error creating vector store: {str(e)}")


# ============================================================================
# 5. RETRIEVAL
# ============================================================================

def retrieve_similar_documents(vector_store: Chroma, query: str,
                                k: int = 3) -> list:
    """
    Perform similarity search against the vector store.

    Args:
        vector_store: Chroma instance
        query: Search query string
        k: Number of results to return

    Returns:
        list[Document]: Top-k most similar documents
    """
    try:
        return vector_store.similarity_search(query, k=k)
    except Exception as e:
        raise Exception(f"❌ Retrieval error: {str(e)}")


def print_retrieval_results(query: str, results: list):
    """Pretty-print retrieval results."""
    print("\n" + "=" * 80)
    print("📋 RETRIEVAL TEST RESULTS")
    print("=" * 80)
    print(f"Query: '{query}'")
    print(f"Retrieved: {len(results)} documents\n")

    for i, doc in enumerate(results, 1):
        print(f"\n--- Document {i} ---")
        print(f"Content:\n{doc.page_content[:500]}...")
        if "source" in doc.metadata:
            print(f"Source: {doc.metadata['source']}")
        if "page" in doc.metadata:
            print(f"Page: {doc.metadata['page']}")

    print("\n" + "=" * 80)


# ============================================================================
# 6. MAIN PIPELINE (standalone execution)
# ============================================================================

def main():
    """
    Full pipeline: load → chunk → embed → store → retrieve.
    Edit pdf_path and test_query below before running.
    """
    print("\n" + "=" * 80)
    print("🚀 RAG PIPELINE — PHASE 1 (Legacy Reference)")
    print("=" * 80 + "\n")

    # ── Configuration ──────────────────────────────────────────────────────────
    pdf_path = "data/paper.pdf"          # ← edit as needed
    db_path = "./Database/chroma_db"
    test_query = "What is the main topic of this document?"

    try:
        print("[1/5] Loading PDF...")
        documents = load_pdf_document(pdf_path)

        print("\n[2/5] Chunking...")
        chunks = chunk_documents(documents, chunk_size=1000, chunk_overlap=200)

        print("\n[3/5] Initializing embeddings...")
        embeddings = initialize_embeddings()

        print("\n[4/5] Creating vector store...")
        vector_store = create_vector_store(chunks, embeddings, db_path)

        print("\n[5/5] Testing retrieval...")
        results = retrieve_similar_documents(vector_store, test_query, k=3)
        print_retrieval_results(test_query, results)

        print("\n✅ Legacy pipeline completed successfully.")
        return vector_store

    except FileNotFoundError as e:
        print(f"\n{e}")
        sys.exit(1)
    except ValueError as e:
        print(f"\n{e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
