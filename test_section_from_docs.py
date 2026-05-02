"""
Benchmark test for generate_section_from_docs().

Tests three retrieval scenarios:
1. related_docs    — Thai content about "embedding"
2. unrelated_docs  — English content about history of Rome
3. ambiguous_docs  — Mix of partially related and unrelated

Runs against the real OpenThaiGPT API using .env credentials.
No Streamlit required.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Force UTF-8 output on Windows so Thai strings display/compare correctly
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Load .env from the project root
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# Verify API key before attempting anything
api_key = os.getenv("OPENTHAI_API_KEY")
if not api_key:
    print("[FATAL] OPENTHAI_API_KEY not found in .env — cannot run test")
    sys.exit(1)

from langchain_core.documents import Document
from generator import generate_section_from_docs


# ── Mock document factories ───────────────────────────────────────────────────

def make_doc(content: str, source_type: str = "document", filename: str = "test.pdf") -> Document:
    return Document(
        page_content=content,
        metadata={
            "source_type": source_type,
            "filename": filename,
            "paper_title": filename,
        },
    )


related_docs = [
    make_doc(
        "Embedding คือกระบวนการแปลงข้อความหรือข้อมูลเป็น vector ตัวเลขที่มีมิติคงที่ "
        "ซึ่งสามารถจับความหมายเชิงอรรถศาสตร์ (semantic meaning) ได้ โมเดล embedding "
        "เช่น Word2Vec, GloVe, BERT และ Sentence-BERT ถูกพัฒนาขึ้นเพื่อแปลงประโยค "
        "ให้เป็น dense vector ที่ประโยคที่มีความหมายใกล้เคียงกันจะมี vector ที่อยู่ใกล้กัน "
        "ในพื้นที่ vector (vector space)",
        filename="embedding_basics_th.pdf",
    ),
    make_doc(
        "การทำงานของ Embedding model แบ่งออกเป็นหลายขั้นตอน ได้แก่ "
        "1) Tokenization: แปลงข้อความเป็น token  "
        "2) Encoding: ส่ง token ผ่าน neural network เพื่อสร้าง contextual representation  "
        "3) Pooling: รวม representation ของแต่ละ token เป็น vector เดียว "
        "โดยทั่วไปใช้ mean pooling หรือ CLS token  "
        "Embedding ที่ดีจะมีคุณสมบัติ isotropy คือ vector กระจายตัวสม่ำเสมอใน vector space",
        filename="embedding_techniques_th.pdf",
    ),
    make_doc(
        "การประยุกต์ใช้ Embedding ในระบบ RAG (Retrieval-Augmented Generation) "
        "เริ่มจากการสร้าง embedding ของเอกสารทั้งหมดล่วงหน้า แล้วเก็บไว้ใน vector database "
        "เช่น Pinecone, Weaviate, หรือ Chroma เมื่อผู้ใช้ส่งคำถาม ระบบจะสร้าง embedding "
        "ของคำถามแล้วค้นหาเอกสารที่มี cosine similarity สูงที่สุด ก่อนส่งให้ LLM สร้างคำตอบ",
        filename="rag_embedding_application_th.pdf",
    ),
]

unrelated_docs = [
    make_doc(
        "The Roman Empire at its height controlled territories stretching from Britain in "
        "the northwest to Mesopotamia in the east. Julius Caesar's assassination in 44 BC "
        "marked a turning point in Roman history, leading to the transformation from "
        "Republic to Empire under Augustus Caesar in 27 BC.",
        filename="history_of_rome_en.pdf",
    ),
    make_doc(
        "The decline of the Western Roman Empire in 476 AD is traditionally attributed to "
        "a combination of military pressures from Germanic tribes, economic troubles, "
        "political instability, and overexpansion. Edward Gibbon's 'The History of the "
        "Decline and Fall of the Roman Empire' (1776) remains a seminal work on this topic.",
        filename="fall_of_rome_en.pdf",
    ),
    make_doc(
        "Roman architecture introduced innovations such as the arch, vault, and dome. "
        "The Pantheon, built around 125 AD under Emperor Hadrian, features an unreinforced "
        "concrete dome that remained the world's largest for over 1,300 years. "
        "The Colosseum, completed in 80 AD, could hold between 50,000 and 80,000 spectators. "
        "Roman roads, aqueducts, and bridges were engineering marvels of the ancient world.",
        filename="roman_architecture_en.pdf",
    ),
]

ambiguous_docs = [
    make_doc(
        "Machine learning models require large amounts of training data to learn meaningful "
        "representations. In natural language processing, pre-trained language models have "
        "revolutionized how computers understand text.",
        filename="ml_overview_en.pdf",
    ),
    make_doc(
        "ประวัติศาสตร์ไทยในยุคสุโขทัยมีความสำคัญอย่างยิ่ง พ่อขุนรามคำแหงมหาราชทรงประดิษฐ์ "
        "อักษรไทยขึ้นในปี พ.ศ. 1826 ซึ่งถือเป็นรากฐานสำคัญของวัฒนธรรมไทย",
        filename="thai_history.pdf",
    ),
    make_doc(
        "Vector databases store high-dimensional vectors and support approximate nearest "
        "neighbor (ANN) search algorithms like HNSW and IVF. These are essential for "
        "building semantic search systems.",
        filename="vector_db_overview_en.pdf",
    ),
]


# ── Test runner ───────────────────────────────────────────────────────────────

TOPIC = "การทำงานของ Embedding"
SECTION = "บทที่ 1: บทนำ"

total_input_tokens = 0
total_output_tokens = 0
results = {}

SCENARIOS = [
    ("related", related_docs),
    ("unrelated", unrelated_docs),
    ("ambiguous", ambiguous_docs),
]

for name, docs in SCENARIOS:
    print(f"\n{'='*60}")
    print(f"Scenario: {name}")
    print(f"Docs: {len(docs)}")
    print(f"{'='*60}")
    try:
        think_text, section_text, ri, ro = generate_section_from_docs(
            topic=TOPIC,
            section_instruction=SECTION,
            retrieved_docs=docs,
            existing_content="",
        )
        total_input_tokens += ri
        total_output_tokens += ro
        results[name] = {
            "think": think_text,
            "text": section_text,
            "ri": ri,
            "ro": ro,
            "error": None,
        }
        print(f"Input tokens : {ri}")
        print(f"Output tokens: {ro}")
        print(f"Output length: {len(section_text)} chars")
        print(f"\n--- Output (first 600 chars) ---")
        print(section_text[:600])
        if len(section_text) > 600:
            print("... [truncated]")
        if think_text:
            print(f"\n--- Think (first 300 chars) ---")
            print(think_text[:300])
    except Exception as e:
        print(f"[ERROR] {e}")
        results[name] = {"text": "", "ri": 0, "ro": 0, "error": str(e)}


# ── Pass/Fail checks ──────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print("PASS/FAIL CHECKS")
print(f"{'='*60}")

checks_passed = 0
checks_total = 0

def check(label: str, condition: bool):
    global checks_passed, checks_total
    checks_total += 1
    status = "PASS" if condition else "FAIL"
    if condition:
        checks_passed += 1
    print(f"  [{status}] {label}")


# Related scenario
r = results.get("related", {})
if r.get("error"):
    check("related: no error", False)
else:
    check("related: output >= 300 chars", len(r["text"]) >= 300)
    # [ความรู้ทั่วไป] should NOT be the dominant pattern (may appear 0-1 times is fine)
    count_general = r["text"].count("[ความรู้ทั่วไป]")
    paragraphs = [p.strip() for p in r["text"].split("\n\n") if p.strip()]
    check(
        f"related: [ความรู้ทั่วไป] not dominant (appears {count_general}x, {len(paragraphs)} paragraphs)",
        count_general < max(1, len(paragraphs) // 2),
    )

# Unrelated scenario
# Expected: model writes from general knowledge (no docs passed to context).
# Either it adds [ความรู้ทั่วไป]/warning labels (ideal), OR at minimum it does
# NOT hallucinate Rome-specific content as Embedding citations.
u = results.get("unrelated", {})
if u.get("error"):
    check("unrelated: no error", False)
else:
    check("unrelated: output >= 100 chars", len(u["text"]) >= 100)
    has_warning = (
        "[ความรู้ทั่วไป]" in u["text"]
        or "คำเตือน" in u["text"]
        or "ไม่พบเอกสาร" in u["text"]
        or "general knowledge" in u["text"].lower()
    )
    # Also pass if the model didn't hallucinate Rome content as Embedding research/citations.
    # Using "Roman Empire" as a text example for Embedding (like "Roman Empire -> vector")
    # is acceptable — what's not acceptable is citing Roman historians or claiming Rome
    # invented Embedding.
    rome_as_citation = (
        "Julius Caesar" in u["text"]
        or "Germanic tribes" in u["text"]
        or "Colosseum" in u["text"]
        or "Edward Gibbon" in u["text"]
    )
    check(
        "unrelated: has warning labels OR no Rome-as-research-citation hallucination",
        has_warning or not rome_as_citation,
    )

# Ambiguous scenario
a = results.get("ambiguous", {})
if a.get("error"):
    check("ambiguous: no error", False)
else:
    check("ambiguous: output >= 300 chars", len(a["text"]) >= 300)


# ── Cost summary ──────────────────────────────────────────────────────────────

total_tokens = total_input_tokens + total_output_tokens
cost_usd = (total_tokens / 1_000_000) * 0.4
cost_thb = cost_usd * 35

print(f"\n{'='*60}")
print("BENCHMARK SUMMARY")
print(f"{'='*60}")
print(f"Total input tokens : {total_input_tokens}")
print(f"Total output tokens: {total_output_tokens}")
print(f"Total tokens       : {total_tokens}")
print(f"Estimated cost     : ${cost_usd:.6f} USD / {cost_thb:.4f} THB")
print(f"Checks passed      : {checks_passed}/{checks_total}")
if checks_passed == checks_total:
    print("RESULT: ALL CHECKS PASSED")
else:
    print(f"RESULT: {checks_total - checks_passed} CHECK(S) FAILED")
