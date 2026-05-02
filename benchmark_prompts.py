"""
Prompt Benchmark Suite — 10 Thai Research Scenarios
Tests all prompt templates across generator.py, reviewer.py, and web_scraper.py.
Measures: input_tokens, output_tokens, latency_ms, cost_thb, quality checks.
"""

import sys
import os
import time
import json
import re

sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
from dotenv import load_dotenv
from dataclasses import dataclass, field
from typing import Callable, List

# Load .env
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Cost constants ──────────────────────────────────────────────────────────
COST_PER_M_TOKENS_USD = 0.4
USD_TO_THB = 35.0

# ── Fake Document class for functions that expect Document objects ──────────
class FakeDoc:
    """Mimics langchain Document with page_content and metadata."""
    def __init__(self, page_content, metadata=None):
        self.page_content = page_content
        self.metadata = metadata or {}


# ── Result dataclass ────────────────────────────────────────────────────────
@dataclass
class BenchmarkResult:
    test_id: int
    name: str
    template: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cost_thb: float = 0.0
    quality_passed: bool = False
    quality_notes: str = ""
    error: str = ""
    raw_output: str = ""


def calc_cost_thb(input_tokens: int, output_tokens: int) -> float:
    total_tokens = input_tokens + output_tokens
    return (total_tokens / 1_000_000) * COST_PER_M_TOKENS_USD * USD_TO_THB


# ── Sample data ─────────────────────────────────────────────────────────────
SAMPLE_RAG_DOCS = [
    FakeDoc(
        "Retrieval-Augmented Generation (RAG) คือเทคนิคที่ผสมผสานการค้นหาข้อมูลจากฐานความรู้ "
        "กับการสร้างคำตอบโดย LLM ช่วยลดปัญหา hallucination และให้คำตอบที่แม่นยำขึ้น "
        "RAG ถูกนำมาใช้ในงานวิจัยเชิงวิชาการ เช่น การสังเคราะห์วรรณกรรม การตอบคำถามจากเอกสาร "
        "และการสร้างบทความวิจัยอัตโนมัติ ข้อดีหลักคือสามารถอ้างอิงแหล่งที่มาได้ชัดเจน",
        {"source_type": "document", "paper_title": "RAG Survey 2024"}
    ),
    FakeDoc(
        "ระบบ RAG ประกอบด้วย 3 ขั้นตอนหลัก: 1) Indexing — แปลงเอกสารเป็น embeddings "
        "2) Retrieval — ค้นหาเอกสารที่เกี่ยวข้อง 3) Generation — สร้างคำตอบจาก LLM "
        "เทคนิคขั้นสูง เช่น Parent-Child Chunking, MMR Retrieval, และ Query Rewriting "
        "ช่วยเพิ่มคุณภาพการค้นหาและคำตอบ",
        {"source_type": "document", "paper_title": "RAG Architecture"}
    ),
]

SAMPLE_CHAT_HISTORY = [
    {"role": "user", "content": "RAG คืออะไร"},
    {"role": "assistant", "content": "RAG (Retrieval-Augmented Generation) คือเทคนิคที่ผสมผสานการค้นหาข้อมูล "
     "จากฐานความรู้กับ LLM เพื่อสร้างคำตอบที่แม่นยำและอ้างอิงได้"},
]

SAMPLE_CHAPTER1 = (
    "บทที่ 1 บทนำ\n\n"
    "1.1 ความเป็นมาและความสำคัญของปัญหา\n\n"
    "ในปัจจุบัน ปัญญาประดิษฐ์ (Artificial Intelligence: AI) ได้เข้ามามีบทบาทสำคัญในหลายภาคส่วน "
    "รวมถึงภาคการศึกษา การนำ AI มาใช้ในระบบการศึกษาไทยมีทั้งโอกาสและความท้าทาย "
    "จากสถิติของกระทรวงศึกษาธิการ พ.ศ. 2566 พบว่ามีสถานศึกษาเพียง 15% ที่มีการนำ AI มาใช้จริง\n\n"
    "1.2 วัตถุประสงค์การวิจัย\n\n"
    "เพื่อศึกษาผลกระทบของ AI ต่อคุณภาพการเรียนการสอนในระดับอุดมศึกษาไทย\n\n"
    "1.3 สมมติฐานการวิจัย\n\n"
    "AI มีผลกระทบเชิงบวกต่อคุณภาพการเรียนรู้ของนักศึกษาระดับปริญญาตรี\n\n"
    "1.4 ขอบเขตการวิจัย\n\n"
    "ศึกษาเฉพาะสถาบันอุดมศึกษาในเขตกรุงเทพมหานคร ปีการศึกษา 2567"
)

SAMPLE_THAI_ARTICLE = (
    "เทคโนโลยี Large Language Model (LLM) กำลังเปลี่ยนแปลงวิธีการทำงานของมนุษย์อย่างมาก "
    "ในประเทศไทย หลายองค์กรเริ่มนำ LLM มาใช้ในงานต่าง ๆ เช่น การบริการลูกค้า การวิเคราะห์ข้อมูล "
    "และการสร้างเนื้อหา จากผลสำรวจของ ETDA ในปี 2567 พบว่า 45% ของบริษัทขนาดใหญ่ในไทย "
    "มีแผนจะนำ AI/LLM มาใช้ภายใน 2 ปี\n\n"
    "อย่างไรก็ตาม ยังมีความท้าทายหลายประการ เช่น ปัญหาด้านภาษาไทย ที่โมเดลส่วนใหญ่ "
    "ถูกพัฒนาจากข้อมูลภาษาอังกฤษเป็นหลัก ทำให้ประสิทธิภาพการทำงานกับภาษาไทยยังไม่ดีเท่าที่ควร "
    "นอกจากนี้ ปัญหาเรื่อง Hallucination หรือการสร้างข้อมูลเท็จ ก็เป็นอีกหนึ่งอุปสรรคสำคัญ\n\n"
    "ทางออกหนึ่งคือการใช้เทคนิค Retrieval-Augmented Generation (RAG) ที่ช่วยให้ AI ตอบคำถาม "
    "โดยอ้างอิงจากฐานข้อมูลที่เชื่อถือได้ ลดปัญหา Hallucination ได้อย่างมีนัยสำคัญ "
    "หน่วยงานวิจัยไทยหลายแห่ง เช่น NECTEC และ VISTEC กำลังพัฒนา LLM ที่รองรับภาษาไทยโดยเฉพาะ "
    "เช่น OpenThaiGPT และ WangchanBERTa ซึ่งเป็นก้าวสำคัญในการพัฒนา AI สำหรับคนไทย"
)

SAMPLE_EXISTING_CONTENT = (
    "# ผลกระทบของ AI ต่อการศึกษาไทย\n\n"
    "## บทที่ 1: บทนำ\n\n"
    "ปัญญาประดิษฐ์ (AI) กำลังเปลี่ยนแปลงระบบการศึกษาทั่วโลก ..."
)


# ── Test definitions ────────────────────────────────────────────────────────

def run_test_1():
    """Research Mode: สร้างงานวิจัย AI+Education"""
    from generator import generate_answer
    action, resp, editor, i_tok, o_tok = generate_answer(
        query="สร้างงานวิจัย เรื่อง ผลกระทบของ AI ต่อการศึกษาไทย",
        retrieved_docs=SAMPLE_RAG_DOCS,
        research_mode=True,
    )
    output = editor or resp
    return output, i_tok, o_tok


def check_quality_1(output):
    checks = []
    if len(output) < 500:
        checks.append(f"Content too short: {len(output)} chars (need >=500)")
    for kw in ["AI", "การศึกษา"]:
        if kw not in output:
            checks.append(f"Missing keyword: {kw}")
    if not checks:
        return True, "OK"
    return False, "; ".join(checks)


def run_test_2():
    """Q&A: RAG คืออะไร"""
    from generator import generate_answer
    action, resp, editor, i_tok, o_tok = generate_answer(
        query="RAG คืออะไร มีประโยชน์อย่างไรในงานวิจัย",
        retrieved_docs=SAMPLE_RAG_DOCS,
    )
    return resp, i_tok, o_tok


def check_quality_2(output):
    checks = []
    if len(output) < 100:
        checks.append(f"Too short: {len(output)} chars")
    for kw in ["RAG", "Retrieval"]:
        if kw not in output:
            checks.append(f"Missing keyword: {kw}")
    if not checks:
        return True, "OK"
    return False, "; ".join(checks)


def run_test_3():
    """Rephrase + Q&A: ถามข้อเสียของ RAG (with chat history)"""
    from generator import generate_answer
    action, resp, editor, i_tok, o_tok = generate_answer(
        query="แล้วข้อเสียล่ะ",
        retrieved_docs=SAMPLE_RAG_DOCS,
        chat_history=SAMPLE_CHAT_HISTORY,
    )
    return resp, i_tok, o_tok


def check_quality_3(output):
    checks = []
    if len(output) < 80:
        checks.append(f"Too short: {len(output)} chars")
    # Check that rephrase worked — output should reference disadvantages/limitations
    negative_keywords = ["ข้อเสีย", "ข้อจำกัด", "ปัญหา", "ความท้าทาย", "จุดอ่อน",
                         "limitation", "disadvantage", "challenge"]
    found = any(kw.lower() in output.lower() for kw in negative_keywords)
    if not found:
        checks.append("Output doesn't discuss disadvantages (rephrase may have failed)")
    if not checks:
        return True, "OK"
    return False, "; ".join(checks)


def run_test_4():
    """Section Gen: บทที่ 2 ทบทวนวรรณกรรม"""
    from generator import generate_section
    content, i_tok, o_tok = generate_section(
        topic="ผลกระทบของ AI ต่อการศึกษาไทย",
        section_instruction="บทที่ 2: ทบทวนวรรณกรรมและเอกสารที่เกี่ยวข้อง ครอบคลุมทฤษฎี AI ในการศึกษา งานวิจัยที่เกี่ยวข้อง และกรอบแนวคิด",
        retrieved_docs=SAMPLE_RAG_DOCS,
    )
    return content, i_tok, o_tok


def check_quality_4(output):
    checks = []
    word_count = len(output.split())
    if word_count < 200:
        checks.append(f"Too short: {word_count} words (need >=200)")
    # Should not be JSON
    if output.strip().startswith("{"):
        checks.append("Output appears to be JSON (should be plain text)")
    if not checks:
        return True, "OK"
    return False, "; ".join(checks)


def run_test_5():
    """Section Gen with existing content: บทที่ 3"""
    from generator import generate_section
    content, i_tok, o_tok = generate_section(
        topic="ผลกระทบของ AI ต่อการศึกษาไทย",
        section_instruction="บทที่ 3: ระเบียบวิธีวิจัย อธิบายรูปแบบการวิจัย ประชากร กลุ่มตัวอย่าง เครื่องมือ และการวิเคราะห์ข้อมูล",
        retrieved_docs=SAMPLE_RAG_DOCS,
        existing_content=SAMPLE_EXISTING_CONTENT,
    )
    return content, i_tok, o_tok


def check_quality_5(output):
    checks = []
    word_count = len(output.split())
    if word_count < 200:
        checks.append(f"Too short: {word_count} words (need >=200)")
    # Should not duplicate existing content
    if "## บทที่ 1: บทนำ" in output:
        checks.append("Duplicated existing content (chapter 1)")
    if not checks:
        return True, "OK"
    return False, "; ".join(checks)


def run_test_6():
    """Selection Edit: make casual Thai academic"""
    from generator import generate_selection_edit
    casual_text = (
        "AI มันเจ๋งมากเลย ช่วยให้เรียนง่ายขึ้นเยอะ "
        "แถมยังทำให้ครูสบายขึ้นด้วย ไม่ต้องตรวจงานเองทั้งหมด "
        "นักเรียนก็ชอบเพราะได้ feedback เร็ว"
    )
    content, i_tok, o_tok = generate_selection_edit(
        selected_text=casual_text,
        instruction="ปรับให้เป็นภาษาวิชาการที่เหมาะสมกับงานวิจัย",
    )
    return content, i_tok, o_tok


def check_quality_6(output):
    checks = []
    casual_words = ["เจ๋ง", "สบาย", "แถม"]
    for cw in casual_words:
        if cw in output:
            checks.append(f"Still contains casual word: '{cw}'")
    if len(output) < 50:
        checks.append(f"Too short: {len(output)} chars")
    if not checks:
        return True, "OK"
    return False, "; ".join(checks)


def run_test_7():
    """Insertion: insert AI education examples at cursor"""
    from generator import generate_insertion
    before = (
        "ปัญญาประดิษฐ์ถูกนำมาใช้ในภาคการศึกษาหลายรูปแบบ "
        "ตัวอย่างเช่น"
    )
    after = (
        "\n\nจากตัวอย่างข้างต้น จะเห็นได้ว่า AI มีศักยภาพในการยกระดับคุณภาพการศึกษาไทย"
    )
    content, i_tok, o_tok = generate_insertion(
        context_before=before,
        context_after=after,
        instruction="ยกตัวอย่าง 3 กรณีการใช้ AI ในการศึกษาไทย พร้อมอธิบายสั้น ๆ",
    )
    return content, i_tok, o_tok


def check_quality_7(output):
    checks = []
    if len(output) < 80:
        checks.append(f"Too short: {len(output)} chars")
    # Should mention education-related terms
    edu_terms = ["การศึกษา", "เรียน", "สอน", "นักเรียน", "นักศึกษา", "ครู", "อาจารย์"]
    found = any(t in output for t in edu_terms)
    if not found:
        checks.append("No education-related terms found")
    if not checks:
        return True, "OK"
    return False, "; ".join(checks)


def run_test_8():
    """Advisor Review: review chapter 1"""
    from reviewer import review_research
    review, i_tok, o_tok = review_research(
        content=SAMPLE_CHAPTER1,
        user_focus="ตรวจสอบความสมบูรณ์ของบทนำ วัตถุประสงค์ และสมมติฐาน",
    )
    return review, i_tok, o_tok


def check_quality_8(output):
    checks = []
    tags = ["[ต้องแก้ไข]", "[ดีแล้ว]", "[คำแนะนำ]"]
    found_tags = [t for t in tags if t in output]
    if not found_tags:
        checks.append("No review tags found ([ต้องแก้ไข]/[ดีแล้ว]/[คำแนะนำ])")
    if len(output) < 200:
        checks.append(f"Too short for a review: {len(output)} chars")
    if not checks:
        return True, "OK"
    return False, "; ".join(checks)


def run_test_9():
    """Web Summarization: summarize Thai tech article"""
    from web_scraper import summarize_content
    result = summarize_content(SAMPLE_THAI_ARTICLE)
    if not result['success']:
        raise RuntimeError(result['error'])
    return result['summary'], result['input_tokens'], result['output_tokens']


def check_quality_9(output):
    checks = []
    if len(output) >= len(SAMPLE_THAI_ARTICLE):
        checks.append("Summary is longer than input")
    key_terms = ["LLM", "RAG"]
    found = any(t in output for t in key_terms)
    if not found:
        checks.append("Missing key terms (LLM/RAG)")
    if len(output) < 50:
        checks.append(f"Too short: {len(output)} chars")
    if not checks:
        return True, "OK"
    return False, "; ".join(checks)


def run_test_10():
    """Web Title: generate title from Thai article"""
    from web_scraper import generate_title
    result = generate_title(SAMPLE_THAI_ARTICLE)
    if not result['success']:
        raise RuntimeError(result['error'])
    return result['title'], result['input_tokens'], result['output_tokens']


def check_quality_10(output):
    checks = []
    if not output.strip():
        checks.append("Title is empty")
    if len(output) > 100:
        checks.append(f"Title too long: {len(output)} chars (max 100)")
    if output.startswith('"') or output.startswith("'"):
        checks.append("Title has wrapping quotes")
    if not checks:
        return True, "OK"
    return False, "; ".join(checks)


# ── Test registry ───────────────────────────────────────────────────────────
TESTS = [
    (1, "Research Mode (AI+Education)", "Research Mode", run_test_1, check_quality_1),
    (2, "Q&A (RAG basics)", "Q&A Answer", run_test_2, check_quality_2),
    (3, "Rephrase+Q&A (RAG drawbacks)", "Rephrase + Q&A", run_test_3, check_quality_3),
    (4, "Section Gen (Ch.2 Lit Review)", "Section Generation", run_test_4, check_quality_4),
    (5, "Section Gen (Ch.3 w/ existing)", "Section Generation", run_test_5, check_quality_5),
    (6, "Selection Edit (casual→formal)", "Selection Edit", run_test_6, check_quality_6),
    (7, "Insertion (AI examples)", "Insertion", run_test_7, check_quality_7),
    (8, "Advisor Review (Ch.1)", "Advisor Review", run_test_8, check_quality_8),
    (9, "Web Summary (Thai article)", "Web Summarization", run_test_9, check_quality_9),
    (10, "Web Title (Thai article)", "Web Title", run_test_10, check_quality_10),
]


# ── Main benchmark runner ──────────────────────────────────────────────────

def run_benchmark(selected_tests=None):
    """Run all (or selected) benchmark tests and return results."""
    results: List[BenchmarkResult] = []

    tests_to_run = TESTS
    if selected_tests:
        tests_to_run = [t for t in TESTS if t[0] in selected_tests]

    total = len(tests_to_run)
    for idx, (test_id, name, template, run_fn, check_fn) in enumerate(tests_to_run):
        print(f"\n{'='*60}")
        print(f"[{idx+1}/{total}] Test #{test_id}: {name}")
        print(f"  Template: {template}")
        print(f"{'='*60}")

        result = BenchmarkResult(test_id=test_id, name=name, template=template)

        try:
            start = time.time()
            output, i_tok, o_tok = run_fn()
            elapsed = (time.time() - start) * 1000

            result.input_tokens = i_tok
            result.output_tokens = o_tok
            result.latency_ms = round(elapsed, 1)
            result.cost_thb = calc_cost_thb(i_tok, o_tok)
            result.raw_output = output[:500] if output else ""

            # Quality check
            passed, notes = check_fn(output)
            result.quality_passed = passed
            result.quality_notes = notes

            print(f"  Tokens: {i_tok} in / {o_tok} out")
            print(f"  Latency: {result.latency_ms:.0f} ms")
            print(f"  Cost: {result.cost_thb:.6f} THB")
            print(f"  Quality: {'PASS' if passed else 'FAIL'} — {notes}")
            if not passed:
                print(f"  Output preview: {output[:200]}...")

        except Exception as e:
            result.error = str(e)
            print(f"  ERROR: {e}")

        results.append(result)

        # Rate limit: 2s between API calls
        if idx < total - 1:
            print("  (waiting 2s for rate limit...)")
            time.sleep(2)

    return results


def print_report(results: List[BenchmarkResult]):
    """Print formatted benchmark report."""
    print("\n")
    print("=" * 100)
    print("PROMPT BENCHMARK REPORT".center(100))
    print("=" * 100)

    # Header
    header = f"{'#':>2} | {'Test Name':<35} | {'Template':<18} | {'In Tok':>7} | {'Out Tok':>7} | {'Cost(THB)':>10} | {'Latency':>8} | {'Quality':>7}"
    print(header)
    print("-" * 100)

    total_cost = 0.0
    total_in = 0
    total_out = 0

    for r in results:
        if r.error:
            quality_str = "ERROR"
        else:
            quality_str = "PASS" if r.quality_passed else "FAIL"

        line = (
            f"{r.test_id:>2} | {r.name:<35} | {r.template:<18} | "
            f"{r.input_tokens:>7} | {r.output_tokens:>7} | "
            f"{r.cost_thb:>10.6f} | {r.latency_ms:>7.0f}ms | {quality_str:>7}"
        )
        print(line)
        total_cost += r.cost_thb
        total_in += r.input_tokens
        total_out += r.output_tokens

    print("-" * 100)
    print(f"{'TOTAL':>2} | {'':<35} | {'':<18} | {total_in:>7} | {total_out:>7} | {total_cost:>10.6f} | {'':<8} |")
    print()

    # Worst performers by cost
    valid = [r for r in results if not r.error]
    if valid:
        by_cost = sorted(valid, key=lambda r: r.cost_thb, reverse=True)
        print("WORST PERFORMERS (by cost):")
        for i, r in enumerate(by_cost[:3]):
            print(f"  {i+1}. #{r.test_id} {r.name} — {r.cost_thb:.6f} THB "
                  f"({r.input_tokens}+{r.output_tokens} tokens)")

    # Worst performers by latency
    if valid:
        by_latency = sorted(valid, key=lambda r: r.latency_ms, reverse=True)
        print("\nSLOWEST (by latency):")
        for i, r in enumerate(by_latency[:3]):
            print(f"  {i+1}. #{r.test_id} {r.name} — {r.latency_ms:.0f} ms")

    # Quality failures
    failures = [r for r in results if not r.quality_passed and not r.error]
    if failures:
        print("\nQUALITY FAILURES:")
        for r in failures:
            print(f"  #{r.test_id} {r.name}: {r.quality_notes}")

    errors = [r for r in results if r.error]
    if errors:
        print("\nERRORS:")
        for r in errors:
            print(f"  #{r.test_id} {r.name}: {r.error}")

    # Optimization recommendations
    print("\nOPTIMIZATION RECOMMENDATIONS:")
    if valid:
        top_cost = by_cost[0]
        print(f"  1. Highest cost: #{top_cost.test_id} {top_cost.name}")
        if top_cost.template == "Advisor Review":
            print("     -> ADVISOR_SYSTEM_PROMPT is ~1,700 chars. Condense chapter rubrics to checklist format.")
        elif top_cost.template == "Research Mode":
            print("     -> Research Mode system prompt has redundant writing rules. Merge constraints.")
        elif top_cost.template == "Section Generation":
            print("     -> Section Gen has 6 rules with overlap. Merge 'no short summary' with '>=300 words'.")

        print(f"  2. Shared: Language instruction repeated across prompts — shorten to 1 line.")
        print(f"  3. Research Mode JSON template has unnecessary whitespace.")

    print("\n" + "=" * 100)
    return results


def save_results_json(results: List[BenchmarkResult], filepath="benchmark_results.json"):
    """Save results to JSON for before/after comparison."""
    data = []
    for r in results:
        data.append({
            "test_id": r.test_id,
            "name": r.name,
            "template": r.template,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "latency_ms": r.latency_ms,
            "cost_thb": r.cost_thb,
            "quality_passed": r.quality_passed,
            "quality_notes": r.quality_notes,
            "error": r.error,
        })
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Results saved to {filepath}")


def compare_results(before_path="benchmark_results_before.json",
                    after_path="benchmark_results_after.json"):
    """Compare before/after benchmark results."""
    with open(before_path, "r", encoding="utf-8") as f:
        before = {r["test_id"]: r for r in json.load(f)}
    with open(after_path, "r", encoding="utf-8") as f:
        after = {r["test_id"]: r for r in json.load(f)}

    print("\n" + "=" * 100)
    print("BEFORE vs AFTER COMPARISON".center(100))
    print("=" * 100)

    header = (f"{'#':>2} | {'Test Name':<30} | "
              f"{'Before In':>9} | {'After In':>8} | {'Δ In':>6} | "
              f"{'Before Cost':>11} | {'After Cost':>10} | {'Δ Cost':>8} | {'Quality'}")
    print(header)
    print("-" * 100)

    total_before_cost = 0.0
    total_after_cost = 0.0

    for tid in sorted(set(before.keys()) | set(after.keys())):
        b = before.get(tid, {})
        a = after.get(tid, {})
        bname = b.get("name", a.get("name", "?"))

        b_in = b.get("input_tokens", 0)
        a_in = a.get("input_tokens", 0)
        d_in = a_in - b_in

        b_cost = b.get("cost_thb", 0.0)
        a_cost = a.get("cost_thb", 0.0)
        d_cost = a_cost - b_cost

        total_before_cost += b_cost
        total_after_cost += a_cost

        b_qual = "PASS" if b.get("quality_passed") else "FAIL"
        a_qual = "PASS" if a.get("quality_passed") else "FAIL"
        qual = f"{b_qual}->{a_qual}"

        d_in_str = f"{d_in:+d}" if d_in != 0 else "0"
        d_cost_str = f"{d_cost:+.6f}" if d_cost != 0 else "0"

        line = (f"{tid:>2} | {bname:<30} | "
                f"{b_in:>9} | {a_in:>8} | {d_in_str:>6} | "
                f"{b_cost:>11.6f} | {a_cost:>10.6f} | {d_cost_str:>8} | {qual}")
        print(line)

    print("-" * 100)
    savings = total_before_cost - total_after_cost
    pct = (savings / total_before_cost * 100) if total_before_cost > 0 else 0
    print(f"Total cost: {total_before_cost:.6f} -> {total_after_cost:.6f} THB "
          f"(saved {savings:.6f} THB, {pct:.1f}%)")
    print("=" * 100)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Prompt Benchmark Suite")
    parser.add_argument("--tests", type=str, default="",
                        help="Comma-separated test IDs to run (e.g., '1,2,8'). Default: all")
    parser.add_argument("--save", type=str, default="",
                        help="Save results to JSON file (e.g., 'benchmark_results_before.json')")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                        help="Compare two result files")
    args = parser.parse_args()

    if args.compare:
        compare_results(args.compare[0], args.compare[1])
    else:
        selected = None
        if args.tests:
            selected = [int(t.strip()) for t in args.tests.split(",")]

        results = run_benchmark(selected)
        print_report(results)

        if args.save:
            save_results_json(results, args.save)
        else:
            save_results_json(results)
