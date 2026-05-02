"""
RAG Pipeline — CLI Entry Point
Production-ready CLI for document ingestion and semantic retrieval.

Modes:
  --ingest PDF_PATH   Load a PDF and store embeddings in Pinecone
  --query  QUERY_TEXT Retrieve documents and generate an AI answer

Optional:
  --user  USER_ID  Pinecone namespace / user ID (default: cli_user)
  --k     K        Top-k results for retrieval (default: 3)
"""

import os
import sys
import argparse

from document_loader import load_pdf_document, chunk_documents, enrich_metadata, create_parent_child_chunks
from vector_store import (
    get_embedding_model,
    get_pinecone_index,
    ingest_documents,
    retrieve_unified,
    print_retrieval_results,
)
from generator import generate_answer, print_generated_answer


# ============================================================================
# INGEST MODE
# ============================================================================

def ingest_mode(pdf_path: str, user_id: str = "cli_user"):
    """
    Load a PDF, chunk it, embed, and store in Pinecone.
    """
    print("\n" + "=" * 80)
    print("RAG PIPELINE — INGEST MODE (Pinecone)")
    print("=" * 80 + "\n")

    try:
        print("[1/3] Loading and chunking PDF...")
        documents = load_pdf_document(pdf_path)
        documents = enrich_metadata(documents, os.path.basename(pdf_path))
        child_chunks, parent_records = create_parent_child_chunks(
            documents, os.path.basename(pdf_path)
        )

        print("\n[2/3] Initializing embeddings model...")
        model = get_embedding_model()

        print("\n[3/3] Upserting to Pinecone...")
        ingest_documents(child_chunks, parent_records, user_id, embedding_model=model)

        print(f"\nIngest complete. Vectors stored in namespace '{user_id}'\n")

    except FileNotFoundError as e:
        print(f"\n{e}")
        sys.exit(1)
    except ValueError as e:
        print(f"\n{e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        sys.exit(1)


# ============================================================================
# QUERY MODE
# ============================================================================

def query_mode(query_text: str, user_id: str = "cli_user", k: int = 3):
    """
    Retrieve from Pinecone and generate an AI answer.
    """
    print("\n" + "=" * 80)
    print("RAG PIPELINE — QUERY MODE (Pinecone)")
    print("=" * 80 + "\n")

    try:
        print("[1/3] Initializing embeddings model...")
        model = get_embedding_model()

        print("\n[2/3] Retrieving from Pinecone...")
        retrieved_docs = retrieve_unified(
            query_text, user_id, k=k, embedding_model=model
        )
        print_retrieval_results(query_text, retrieved_docs)

        print("\n[3/3] Generating AI answer...")
        _action, answer, _new_editor, input_tokens, output_tokens = generate_answer(
            query_text, retrieved_docs, chat_history=[]
        )
        print_generated_answer(query_text, answer)
        print(f"Tokens used — input: {input_tokens}, output: {output_tokens}\n")
        print("Query completed successfully!\n")

    except ValueError as e:
        print(f"\n{e}")
        print("Hint: Run ingestion first:\n   python main.py --ingest your_document.pdf")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        sys.exit(1)


# ============================================================================
# CLI ARGUMENT PARSER
# ============================================================================

def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="RAG Pipeline CLI — Document Ingestion & Semantic Retrieval (Pinecone)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:

  Ingest a PDF:
    python main.py --ingest data/paper.pdf
    python main.py --ingest documents/paper.pdf --user my_user

  Query the vector store:
    python main.py --query "What is the main finding?"
    python main.py --query "What is consciousness?" --user my_user --k 5
        """
    )

    parser.add_argument(
        "--ingest",
        type=str, metavar="PDF_PATH",
        help="Ingest mode: chunk and embed a PDF into Pinecone"
    )
    parser.add_argument(
        "--query",
        type=str, metavar="QUERY_TEXT",
        help="Query mode: retrieve documents and generate an answer"
    )
    parser.add_argument(
        "--user",
        type=str, default="cli_user", metavar="USER_ID",
        help="Pinecone namespace / user ID (default: cli_user)"
    )
    parser.add_argument(
        "--k",
        type=int, default=3, metavar="K",
        help="Number of results to retrieve (default: 3)"
    )

    return parser


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    parser = create_parser()
    args = parser.parse_args()

    if not args.ingest and not args.query:
        parser.print_help()
        sys.exit(1)

    if args.ingest and args.query:
        print("Error: --ingest and --query are mutually exclusive.")
        sys.exit(1)

    if args.ingest:
        ingest_mode(args.ingest, user_id=args.user)
    else:
        query_mode(args.query, user_id=args.user, k=args.k)


if __name__ == "__main__":
    main()
