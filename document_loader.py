"""
Document Loader Module
Handles PDF, TXT, DOCX/DOC loading, text chunking, and Advanced RAG preparation
(rich metadata, parent-child chunking, adaptive chunk sizing).
"""

import os
import re
import uuid

import requests as _requests
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader, TextLoader, Docx2txtLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document


def load_document(file_path):
    """
    Load a document based on its file extension.
    Supports: .pdf, .txt, .docx, .doc

    Args:
        file_path (str): Path to the document file

    Returns:
        list: List of Document objects

    Raises:
        FileNotFoundError: If the file doesn't exist
        ValueError: If the file type is unsupported or file is empty
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"❌ File not found: {file_path}")

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".pdf":
            loader = PyPDFLoader(file_path)
        elif ext == ".txt":
            try:
                loader = TextLoader(file_path, encoding="utf-8")
            except Exception:
                loader = TextLoader(file_path, encoding="cp874")  # Thai Windows encoding fallback
        elif ext in (".docx", ".doc"):
            loader = Docx2txtLoader(file_path)
        else:
            raise ValueError(f"❌ Unsupported file type: {ext}. Supported: .pdf, .txt, .docx, .doc")

        documents = loader.load()

        if not documents:
            raise ValueError(f"❌ File is empty or could not be parsed: {file_path}")

        print(f"✓ Loaded {ext} file: {os.path.basename(file_path)} ({len(documents)} page(s)/section(s))")
        return documents

    except (FileNotFoundError, ValueError):
        raise
    except Exception as e:
        raise Exception(f"❌ Error loading {os.path.basename(file_path)}: {str(e)}")


def load_pdf_document(pdf_path):
    """
    Load a PDF document using PyPDFLoader (fixed import from langchain_community).
    
    Args:
        pdf_path (str): Path to the PDF file
        
    Returns:
        list: List of Document objects from the PDF
        
    Raises:
        FileNotFoundError: If the PDF file doesn't exist
        Exception: If PDF loading fails
    """
    # Validate that the PDF file exists before attempting to load
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(
            f"❌ PDF file not found at: {pdf_path}\n"
            f"Please ensure the file exists in your working directory."
        )
    
    try:
        # Initialize PyPDFLoader with the PDF path
        # Using the correct import: langchain_community.document_loaders
        loader = PyPDFLoader(pdf_path)
        
        # Load all pages as Document objects
        documents = loader.load()
        
        if not documents:
            raise ValueError(f"❌ PDF '{pdf_path}' is empty or could not be parsed")
        
        print(f"✓ Successfully loaded PDF: {pdf_path}")
        print(f"✓ Total pages loaded: {len(documents)}")
        
        return documents
    
    except Exception as e:
        raise Exception(f"❌ Error loading PDF: {str(e)}")


_splitter_cache = {}


def _get_splitter(chunk_size, chunk_overlap, separators=None):
    """Return a cached RecursiveCharacterTextSplitter for the given params."""
    if separators is None:
        separators = ["\n\n", "\n", " ", ""]
    key = (chunk_size, chunk_overlap, tuple(separators))
    if key not in _splitter_cache:
        _splitter_cache[key] = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators,
        )
    return _splitter_cache[key]


def chunk_documents(documents, chunk_size=1000, chunk_overlap=200):
    """
    Split documents into overlapping chunks for better embedding context.
    Uses RecursiveCharacterTextSplitter to maintain semantic coherence.

    Args:
        documents (list): List of Document objects from the loader
        chunk_size (int): Maximum characters per chunk (default: 1000)
        chunk_overlap (int): Characters to overlap between chunks (default: 200)

    Returns:
        list: List of chunked Document objects

    Raises:
        ValueError: If documents list is empty
        Exception: If chunking fails
    """
    if not documents:
        raise ValueError("❌ No documents provided for chunking")

    try:
        text_splitter = _get_splitter(chunk_size, chunk_overlap)

        chunked_documents = text_splitter.split_documents(documents)

        if not chunked_documents:
            raise ValueError("❌ Chunking resulted in no documents")

        print(f"✓ Document chunking completed")
        print(f"✓ Total chunks created: {len(chunked_documents)}")
        print(f"✓ Chunk size: {chunk_size} chars | Overlap: {chunk_overlap} chars")

        return chunked_documents

    except Exception as e:
        raise Exception(f"❌ Error chunking documents: {str(e)}")


# ── Advanced RAG: Rich Metadata ──────────────────────────────────────────────

_SECTION_PATTERNS = [
    (re.compile(r'\babstract\b'), 'abstract'),
    (re.compile(r'\bintroduction\b'), 'introduction'),
    (re.compile(r'\bliterature\s+review\b'), 'literature_review'),
    (re.compile(r'\brelated\s+work\b'), 'related_work'),
    (re.compile(r'\bmethodology\b|\bmethod\b|\bmethods\b'), 'methodology'),
    (re.compile(r'\bresults?\b'), 'results'),
    (re.compile(r'\bdiscussion\b'), 'discussion'),
    (re.compile(r'\bconclusion\b'), 'conclusion'),
    (re.compile(r'\breferences?\b|\bbibliography\b'), 'references'),
    (re.compile(r'\backnowledg'), 'acknowledgements'),
    (re.compile(r'\bappendix\b|\bappendices\b'), 'appendix'),
    (re.compile(r'\bบทคัดย่อ\b'), 'abstract'),
    (re.compile(r'\bบทนำ\b'), 'introduction'),
    (re.compile(r'\bวิธีการ\b|\bระเบียบวิธี\b'), 'methodology'),
    (re.compile(r'\bผลการ\b'), 'results'),
    (re.compile(r'\bสรุป\b'), 'conclusion'),
    (re.compile(r'\bเอกสารอ้างอิง\b|\bบรรณานุกรม\b'), 'references'),
]


def _detect_section(text: str) -> str:
    """Detect common academic paper sections from text content."""
    text_lower = text[:200].lower().strip()
    for pattern, section in _SECTION_PATTERNS:
        if pattern.search(text_lower):
            return section
    return 'body'


_NON_NAME_WORDS = frozenset({
    "reproduce", "paper", "journal", "copyright", "permission",
    "abstract", "university", "figure", "table", "chapter",
    "solely", "section", "volume", "published", "licensed",
    "distributed", "article", "conference", "proceedings",
})


def _is_valid_author_name(name: str) -> bool:
    """Check if extracted text looks like actual human author names."""
    if not name or not name.strip():
        return False
    name = name.strip()
    if len(name) > 200:
        return False
    if len(name.split()) > 30:
        return False
    name_lower = name.lower()
    if any(w in name_lower for w in _NON_NAME_WORDS):
        return False
    return True


def _extract_authors_with_llm(first_page_text: str) -> str:
    """
    Use OpenThaiGPT to extract author names from a paper's first page.
    Returns extracted author string, or 'unknown' on failure.
    Makes direct API call to avoid circular imports with generator.py.
    """
    load_dotenv()
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        return "unknown"

    # Only send first 800 chars to minimize tokens
    text_snippet = first_page_text[:800]

    messages = [
        {
            "role": "system",
            "content": (
                "Extract the author names from this academic paper's first page. "
                "Rules:\n"
                "1. Return ONLY the author names, separated by commas\n"
                "2. Author names are human names (e.g., 'John Smith, Jane Doe')\n"
                "3. If you cannot identify author names, return exactly: unknown\n"
                "4. Do not include affiliations, emails, or other text\n"
                "5. Return the names exactly as they appear in the paper"
            ),
        },
        {"role": "user", "content": text_snippet},
    ]

    try:
        resp = _requests.post(
            "http://thaillm.or.th/api/openthaigpt/v1/chat/completions",
            headers={"Content-Type": "application/json", "apikey": api_key},
            json={
                "model": "/model",
                "messages": messages,
                "max_tokens": 256,
                "temperature": 0.1,
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip <think> tags if present
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

        # Validate the LLM result
        if not content or content.lower() == "unknown" or not _is_valid_author_name(content):
            return "unknown"
        return content
    except Exception:
        return "unknown"


def _extract_paper_metadata(documents: list, filename: str) -> dict:
    """Extract paper-level metadata from the first page of a document."""
    meta = {'paper_title': '', 'authors': '', 'year': ''}
    if not documents:
        return meta
    first_page = documents[0].page_content[:1500]
    lines = [l.strip() for l in first_page.split('\n') if l.strip()]

    # Heuristic: first non-empty line is likely the title
    if lines:
        meta['paper_title'] = lines[0][:200]

    # Try to find year (4-digit number in range 1900-2099)
    year_match = re.search(r'\b(19|20)\d{2}\b', first_page)
    if year_match:
        meta['year'] = year_match.group()

    # Authors heuristic: second non-empty line or line with commas/and
    for line in lines[1:5]:
        if re.search(r'(,.*,|and\s|\bet\s+al)', line, re.IGNORECASE) or '@' in line:
            meta['authors'] = line[:300]
            break

    # Validate heuristic result; fall back to LLM extraction if suspicious
    if not _is_valid_author_name(meta['authors']):
        meta['authors'] = _extract_authors_with_llm(first_page)

    return meta


def enrich_metadata(documents: list, filename: str, source_type: str = "document",
                    user_id: str = None, project_id: str = None) -> list:
    """
    Add rich metadata to each document page: paper_title, authors, year, section,
    source_type, user_id, project_id, created_at.

    Args:
        documents: List of Document objects (one per page)
        filename: Original filename
        source_type: "document" or "note"
        user_id: Optional user identifier
        project_id: Optional project identifier

    Returns:
        list: Same documents with enriched metadata
    """
    from datetime import date
    paper_meta = _extract_paper_metadata(documents, filename)

    for doc in documents:
        doc.metadata['source_type'] = source_type
        doc.metadata['doc_name'] = filename
        doc.metadata['paper_title'] = paper_meta['paper_title']
        doc.metadata['authors'] = paper_meta['authors']
        doc.metadata['year'] = paper_meta['year']
        doc.metadata['section'] = _detect_section(doc.page_content)
        doc.metadata['created_at'] = date.today().isoformat()
        if user_id:
            doc.metadata['user_id'] = user_id
        if project_id:
            doc.metadata['project_id'] = project_id

    return documents


# ── Advanced RAG: Adaptive Chunk Sizing ──────────────────────────────────────

def get_adaptive_chunk_params(total_chars: int) -> tuple:
    """
    Return (chunk_size, chunk_overlap) adapted to content length.

    - Short content (<2000 chars): small chunks for precision
    - Medium content (2000–10000 chars): standard chunks
    - Long content (>10000 chars): larger chunks for context
    """
    if total_chars < 2000:
        return 300, 50
    elif total_chars < 10000:
        return 800, 150
    else:
        return 1200, 250


# ── Advanced RAG: Parent-Child Chunking ──────────────────────────────────────

def create_parent_child_chunks(documents: list, filename: str,
                                source_type: str = "document",
                                user_id: str = None,
                                project_id: str = None) -> tuple:
    """
    Create parent-child chunk structure for Advanced RAG.

    - Parent chunks: full page/large paragraphs stored in SQLite (for retrieval context)
    - Child chunks: small sentences stored in Pinecone (for vector search)
    - Each child references its parent_id in metadata

    Args:
        documents: List of Document objects (one per page, already enriched)
        filename: Original filename
        source_type: "document" or "note"

    Returns:
        tuple: (child_documents, parent_records)
            child_documents: list of small Document objects for Pinecone
            parent_records: list of dicts {id, content, source_file, page_number, section}
    """
    from datetime import date

    # Determine adaptive chunk size based on total content
    total_chars = sum(len(doc.page_content) for doc in documents)
    child_size, child_overlap = get_adaptive_chunk_params(total_chars)

    child_splitter = _get_splitter(
        child_size, child_overlap,
        separators=["\n\n", "\n", "\u3002", ".", " ", ""],
    )

    parent_records = []
    today = date.today().isoformat()

    # Tag each doc with its parent_id before batch splitting
    for doc in documents:
        parent_id = f"parent_{uuid.uuid4().hex[:12]}"
        page_num = doc.metadata.get('page', 0)
        section = doc.metadata.get('section', 'body')

        parent_records.append({
            'id': parent_id,
            'content': doc.page_content,
            'source_file': filename,
            'page_number': page_num,
            'section': section,
        })

        # Temporarily inject parent_id so children inherit it
        doc.metadata['_parent_id'] = parent_id

    # Batch split all documents at once (avoids per-doc overhead)
    all_children = child_splitter.split_documents(documents)

    # Enrich child metadata
    for child in all_children:
        child.metadata['parent_id'] = child.metadata.pop('_parent_id')
        child.metadata['chunk_type'] = 'child'
        child.metadata['source_type'] = source_type
        child.metadata['doc_name'] = filename
        child.metadata['created_at'] = today
        if user_id:
            child.metadata['user_id'] = user_id
        if project_id:
            child.metadata['project_id'] = project_id

    # Clean up temp key from parent docs
    for doc in documents:
        doc.metadata.pop('_parent_id', None)

    print(f"\u2713 Parent-Child chunking: {len(parent_records)} parents, "
          f"{len(all_children)} children (child_size={child_size})")
    return all_children, parent_records


def create_summary_documents(documents: list, filename: str,
                              generate_summary_fn=None) -> list:
    """
    Create summary embeddings for each page/section.

    If generate_summary_fn is provided, it will be called with the page text
    to produce an AI summary. Otherwise, a simple extractive summary (first 300 chars)
    is used as a fallback.

    Args:
        documents: List of Document objects
        filename: Source filename
        generate_summary_fn: Optional callable(text) -> summary_text

    Returns:
        list: Summary Document objects to be added to the vector store
    """
    from datetime import date
    summary_docs = []

    for doc in documents:
        page_num = doc.metadata.get('page', 0)
        section = doc.metadata.get('section', 'body')

        if generate_summary_fn:
            try:
                summary_text = generate_summary_fn(doc.page_content)
            except Exception:
                # Fallback to extractive summary on error
                summary_text = doc.page_content[:300].strip()
        else:
            summary_text = doc.page_content[:300].strip()

        if not summary_text:
            continue

        summary_doc = Document(
            page_content=summary_text,
            metadata={
                'chunk_type': 'summary',
                'source_type': doc.metadata.get('source_type', 'document'),
                'doc_name': filename,
                'page': page_num,
                'section': section,
                'paper_title': doc.metadata.get('paper_title', ''),
                'created_at': date.today().isoformat(),
            }
        )
        summary_docs.append(summary_doc)

    print(f"✓ Created {len(summary_docs)} summary embeddings for {filename}")
    return summary_docs
