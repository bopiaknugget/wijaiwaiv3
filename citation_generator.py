"""
Auto Citation Generator — APA7 format with LLM-powered inline citation insertion.

Extracts citation metadata from Pinecone vector store based on editor content,
uses OpenThaiGPT to insert [Author, Year] inline citations into the text,
then formats APA7 reference entries.
"""

import os
import re
from difflib import SequenceMatcher
from typing import Optional

from dotenv import load_dotenv

import database
from generator import _call_api
from vector_store import retrieve_unified


def extract_citation_metadata(
    editor_content: str,
    user_id: str,
    max_paragraphs: int = 10,
) -> list[dict]:
    """
    Extract citation metadata by matching editor paragraphs against the knowledge base.

    Splits editor content into paragraphs (non-empty, >50 chars), samples up to
    max_paragraphs evenly spaced, retrieves matching documents from Pinecone,
    and deduplicates by doc_name.

    Args:
        editor_content: The full text from the Research Workbench editor.
        user_id: Google user ID for namespace scoping.
        max_paragraphs: Maximum number of paragraphs to sample.

    Returns:
        List of dicts with keys: paper_title, authors, year, doc_name.
    """
    if not editor_content or not editor_content.strip():
        return []

    # Split into meaningful paragraphs
    paragraphs = [
        p.strip() for p in editor_content.split("\n")
        if p.strip() and len(p.strip()) > 50
    ]

    if not paragraphs:
        return []

    # Evenly sample paragraphs if there are more than max_paragraphs
    if len(paragraphs) > max_paragraphs:
        step = len(paragraphs) / max_paragraphs
        sampled = [paragraphs[int(i * step)] for i in range(max_paragraphs)]
    else:
        sampled = paragraphs

    # Retrieve matching documents for each paragraph
    seen_doc_names: set[str] = set()
    results: list[dict] = []

    for paragraph in sampled:
        docs = retrieve_unified(
            paragraph, user_id, k=2, source_type="document"
        )
        for doc in docs:
            meta = doc.metadata
            doc_name = meta.get("doc_name", "")
            if not doc_name or doc_name in seen_doc_names:
                continue
            seen_doc_names.add(doc_name)
            results.append({
                "paper_title": meta.get("paper_title", ""),
                "authors": meta.get("authors", ""),
                "year": meta.get("year", ""),
                "doc_name": doc_name,
            })

    return results


def format_apa7_reference(meta: dict) -> str:
    """
    Format a single APA7 reference entry from metadata.

    Handles missing fields gracefully:
    - No year -> "n.d."
    - No authors -> use doc_name as author
    - No title -> use doc_name as title

    Args:
        meta: Dict with keys paper_title, authors, year, doc_name.

    Returns:
        Formatted APA7 reference string.
    """
    authors = meta.get("authors", "").strip()
    year = meta.get("year", "").strip()
    title = meta.get("paper_title", "").strip()
    doc_name = meta.get("doc_name", "Unknown Document").strip()

    # Fallbacks
    if not authors:
        authors = doc_name
    if not year:
        year = "n.d."
    if not title:
        title = doc_name

    return f"{authors} ({year}). {title}."


def format_apa7_in_text(meta: dict) -> str:
    """
    Format an APA7 in-text citation from metadata.

    Examples:
        (Smith, 2023)
        (Unknown, n.d.)

    Args:
        meta: Dict with keys authors, year, doc_name.

    Returns:
        Parenthetical in-text citation string.
    """
    authors = meta.get("authors", "").strip()
    year = meta.get("year", "").strip()
    doc_name = meta.get("doc_name", "Unknown").strip()

    if not authors:
        authors = doc_name
    if not year:
        year = "n.d."

    # For in-text, use only the first author's surname if multiple
    first_author = authors.split(",")[0].strip()
    if " & " in authors or ", " in authors:
        # Multiple authors — use "et al." if more than 2
        author_parts = [a.strip() for a in authors.replace(" & ", ", ").split(",") if a.strip()]
        if len(author_parts) > 2:
            first_author = f"{author_parts[0]} et al."
        elif len(author_parts) == 2:
            first_author = f"{author_parts[0]} & {author_parts[1]}"

    return f"({first_author}, {year})"


# ── Author name validation ───────────────────────────────────────────────────

_NON_NAME_WORDS = frozenset({
    "reproduce", "paper", "journal", "copyright", "permission",
    "abstract", "university", "figure", "table", "chapter",
    "solely", "section", "volume", "published", "licensed",
    "distributed", "article", "conference", "proceedings",
})


def _is_valid_author_name_simple(name: str) -> bool:
    """Quick check if metadata author field looks like real human names."""
    if not name or not name.strip():
        return False
    if len(name) > 200 or len(name.split()) > 30:
        return False
    name_lower = name.lower()
    return not any(w in name_lower for w in _NON_NAME_WORDS)


# ── New functions for LLM-powered inline citation insertion ──────────────────


def extract_paragraph_citations(
    editor_content: str,
    user_id: str,
    score_threshold: float = 0.45,
) -> tuple[list[str], dict[int, list[dict]]]:
    """
    Split editor content into lines and find RAG matches for substantial lines.

    Args:
        editor_content: Full editor text.
        user_id: Google user ID for Pinecone namespace.
        score_threshold: Minimum retrieval score to consider a match.

    Returns:
        (all_lines, match_map) where match_map maps line index to list of
        matched source dicts with keys: meta, score, content_snippet.
    """
    all_lines = editor_content.split("\n")
    match_map: dict[int, list[dict]] = {}

    for idx, line in enumerate(all_lines):
        if len(line.strip()) <= 50:
            continue

        results = retrieve_unified(
            line.strip(), user_id, k=3,
            source_type="document", return_scores=True,
        )

        matched_sources: list[dict] = []
        seen_docs_in_line: set[str] = set()

        for doc, score in results:
            if score < score_threshold:
                continue
            meta = doc.metadata
            doc_name = meta.get("doc_name", "")

            # Deduplicate: keep only highest-scoring occurrence per doc per line
            if doc_name in seen_docs_in_line:
                continue
            seen_docs_in_line.add(doc_name)

            authors = meta.get("authors", "")
            if not _is_valid_author_name_simple(authors):
                authors = "unknown"

            matched_sources.append({
                "meta": {
                    "paper_title": meta.get("paper_title", ""),
                    "authors": authors,
                    "year": meta.get("year", ""),
                    "doc_name": doc_name,
                },
                "score": score,
                "content_snippet": doc.page_content[:200],
            })

        if matched_sources:
            match_map[idx] = matched_sources

    return all_lines, match_map


def _build_citation_prompt(
    paragraph: str,
    matched_sources: list[dict],
) -> list[dict]:
    """
    Build the LLM messages for inserting inline citations into a paragraph.

    Args:
        paragraph: The paragraph text to add citations to.
        matched_sources: List of matched source dicts from extract_paragraph_citations.

    Returns:
        List of message dicts for the OpenThaiGPT API.
    """
    system_msg = (
        "You are a citation insertion assistant. Your ONLY job is to insert "
        "inline citations into the given paragraph. Strict rules:\n"
        "1. Insert citations in format [Author, Year] where content matches a source\n"
        "2. DO NOT change, rephrase, add, or remove ANY other text\n"
        "3. Place citation at end of the sentence or clause that contains "
        "information from the source\n"
        "4. If the paragraph content does not clearly match any source, "
        "return the paragraph UNCHANGED\n"
        "5. Use the EXACT author name and year provided in the sources below\n"
        "6. Return ONLY the paragraph text with citations inserted. "
        "No explanations, no preamble."
    )

    source_lines = []
    for i, src in enumerate(matched_sources, 1):
        m = src["meta"]
        authors = m.get("authors", "Unknown")
        year = m.get("year", "n.d.")
        title = m.get("paper_title", m.get("doc_name", ""))
        snippet = src.get("content_snippet", "")
        source_lines.append(
            f"{i}. Author: {authors} | Year: {year} | Title: {title}\n"
            f"   Content: \"{snippet}\""
        )

    user_msg = (
        f"=== PARAGRAPH ===\n{paragraph}\n\n"
        f"=== AVAILABLE SOURCES ===\n"
        + "\n".join(source_lines)
        + "\n\nReturn the paragraph with [Author, Year] citations inserted:"
    )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _validate_citation_insertion(original: str, cited: str) -> bool:
    """
    Validate that the LLM only inserted citations and did not alter content.

    Strips all [...] patterns from the cited text and compares to original.
    Passes if SequenceMatcher ratio >= 0.95.

    Args:
        original: The original paragraph text.
        cited: The LLM-returned paragraph with citations.

    Returns:
        True if the cited text is essentially the original plus citations only.
    """
    stripped = re.sub(r'\[.*?\]', '', cited)
    # Normalize whitespace
    orig_norm = ' '.join(original.split())
    stripped_norm = ' '.join(stripped.split())
    ratio = SequenceMatcher(None, orig_norm, stripped_norm).ratio()
    return ratio >= 0.95


def _strip_unknown_citations(text: str, known_citations: set[str]) -> str:
    """
    Remove any [...] patterns from text that are not in the known citations set.

    Args:
        text: Text potentially containing citation markers.
        known_citations: Set of valid citation strings like "(Author, Year)".

    Returns:
        Text with unknown citation markers removed.
    """
    def _replace(match):
        token = match.group(0)
        # Convert [Author, Year] to (Author, Year) for comparison
        inner = token[1:-1]  # strip brackets
        paren_form = f"({inner})"
        if paren_form in known_citations:
            return token
        # Also check bracket form directly
        if token in known_citations:
            return token
        return ""

    return re.sub(r'\[[^\]]+?\]', _replace, text)


def _fallback_append_citation(paragraph: str, sources: list[dict]) -> str:
    """
    Fallback: append all matching citations at the end of a paragraph.

    Used when LLM validation fails.

    Args:
        paragraph: Original paragraph text.
        sources: List of matched source dicts.

    Returns:
        Paragraph with citations appended at the end.
    """
    citations = [format_apa7_in_text(s["meta"]) for s in sources]
    # Convert parenthetical to bracket form for inline: (Author, Year) -> [Author, Year]
    bracket_citations = [c.replace("(", "[").replace(")", "]") for c in citations]
    return paragraph + " " + " ".join(bracket_citations)


def generate_citation_output(
    editor_content: str,
    user_id: str,
) -> tuple[Optional[str], str, list[dict]]:
    """
    Main entry point for auto citation generation with LLM-powered inline insertion.

    Extracts paragraph-level RAG matches, uses OpenThaiGPT to insert [Author, Year]
    inline citations, then builds the APA7 reference list.

    Args:
        editor_content: The full text from the Research Workbench editor.
        user_id: Google user ID for namespace scoping.

    Returns:
        Tuple of (cited_content, reference_markdown, sources_list).
        cited_content is the full text with inline citations inserted (None if no matches).
        reference_markdown is the APA7 reference list.
        sources_list is the raw metadata list.
    """
    all_lines, match_map = extract_paragraph_citations(editor_content, user_id)

    if not match_map:
        no_match_msg = (
            "## รายการอ้างอิง (Auto Citation - APA7)\n\n"
            "ไม่พบเอกสารที่ตรงกับเนื้อหาใน Knowledge Base\n\n"
            "**คำแนะนำ:**\n"
            "- ตรวจสอบว่ามีเอกสารอัปโหลดใน Knowledge Base แล้ว\n"
            "- เนื้อหาในตัวแก้ไขควรเกี่ยวข้องกับเอกสารที่อัปโหลด\n"
        )
        return None, no_match_msg, []

    # Load API key
    load_dotenv()
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        return None, "**Error:** ไม่พบ OPENTHAI_API_KEY ใน .env", []

    # Collect all unique sources across all matched lines
    all_sources: dict[str, dict] = {}  # keyed by doc_name for dedup
    for sources_list in match_map.values():
        for src in sources_list:
            doc_name = src["meta"].get("doc_name", "")
            if doc_name and doc_name not in all_sources:
                all_sources[doc_name] = src["meta"]

    unique_sources = list(all_sources.values())

    # Build known citations set (parenthetical form from format_apa7_in_text)
    known_citations: set[str] = set()
    for src_meta in unique_sources:
        known_citations.add(format_apa7_in_text(src_meta))

    # Process each matched paragraph through LLM
    cited_lines: dict[int, str] = {}

    for idx, matched_sources in match_map.items():
        paragraph = all_lines[idx]
        messages = _build_citation_prompt(paragraph, matched_sources)

        try:
            content, input_tokens, output_tokens = _call_api(
                messages, api_key, max_tokens=2048, temperature=0.1,
            )
            # Track token usage
            database.record_token_usage(
                user_id, input_tokens, output_tokens, "auto_citation",
            )

            # Clean up LLM response (strip leading/trailing whitespace)
            cited_text = content.strip()

            # Strip <think> tags if present
            cited_text = re.sub(
                r'<think>.*?</think>', '', cited_text, flags=re.DOTALL
            ).strip()

            # Validate that LLM didn't alter the content
            if _validate_citation_insertion(paragraph, cited_text):
                cited_text = _strip_unknown_citations(cited_text, known_citations)
                cited_lines[idx] = cited_text
            else:
                # Fallback: append citations at end
                cited_lines[idx] = _fallback_append_citation(
                    paragraph, matched_sources,
                )
        except Exception as e:
            print(f"Warning: LLM citation call failed for line {idx}: {e}")
            # Fallback on API error
            cited_lines[idx] = _fallback_append_citation(
                paragraph, matched_sources,
            )

    # Reconstruct full content with cited versions where available
    result_lines = []
    for idx, line in enumerate(all_lines):
        if idx in cited_lines:
            result_lines.append(cited_lines[idx])
        else:
            result_lines.append(line)

    cited_content = "\n".join(result_lines)

    # Build reference markdown
    ref_lines = []
    for i, src_meta in enumerate(unique_sources, 1):
        ref = format_apa7_reference(src_meta)
        ref_lines.append(f"{i}. {ref}")

    in_text_lines = []
    for i, src_meta in enumerate(unique_sources, 1):
        in_text = format_apa7_in_text(src_meta)
        title = src_meta.get("paper_title") or src_meta.get("doc_name", "")
        in_text_lines.append(f"{i}. **{title}** — {in_text}")

    ref_markdown = (
        "## รายการอ้างอิงที่พบ (In-Text Citations)\n\n"
        + "\n".join(in_text_lines)
        + "\n\n"
        + "## References\n\n"
        + "\n".join(ref_lines)
        + "\n"
    )

    return cited_content, ref_markdown, unique_sources
