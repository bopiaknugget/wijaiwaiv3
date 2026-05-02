"""
Research Reviewer Module — Strict Advisor Persona
Reviews research writing quality as a rigorous thesis advisor,
providing color-coded feedback (red/green/yellow).
"""

import json
import os
import re
import requests
from pathlib import Path
from typing import Generator
from dotenv import load_dotenv

API_URL = "http://thaillm.or.th/api/openthaigpt/v1/chat/completions"
MODEL = "/model"
_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)


def _call_api(messages, api_key, max_tokens=4096, temperature=0.3):
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key,
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    response = requests.post(API_URL, headers=headers, json=payload, timeout=120)
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return content, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def _call_api_stream(messages, api_key, max_tokens=4096,
                     temperature=0.3) -> Generator[str, None, None]:
    """
    Stream tokens from OpenThaiGPT via SSE (mirrors generator._call_api_stream).
    Yields individual token strings. Falls back to single-chunk yield if the
    server returns a non-streaming response.
    """
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key,
        "Accept": "text/event-stream",
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    payload_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        with requests.post(
            API_URL, headers=headers, data=payload_body,
            timeout=120, stream=True
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if "text/event-stream" not in content_type and "stream" not in content_type:
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    yield content
                return
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                if raw_line.startswith("data: "):
                    data_str = raw_line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = (
                            chunk.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content", "")
                        )
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
    except requests.RequestException:
        raise


PAPER_COMPARISON_SYSTEM_PROMPT = """\
คุณคือ "นักวิจารณ์วิชาการอาวุโส" ประสบการณ์ 20+ ปี ในการประเมินงานวิจัยไทยและนานาชาติ
ภารกิจ: วิเคราะห์เชิงวิพากษ์งานวิจัยที่ให้มาในทุกมิติสำคัญ และเปรียบเทียบระหว่างงานหากมีหลายชิ้น
หมายเหตุ: งานวิจัยอาจเป็นภาษาไทยหรือภาษาอังกฤษ — ให้คงศัพท์เทคนิคภาษาอังกฤษไว้ในวงเล็บเมื่อจำเป็น

โครงสร้างผลลัพธ์ที่ต้องการ (ใช้ Markdown อย่างเคร่งครัด):

## ภาพรวม
[สรุปสั้น 2-3 ประโยคเกี่ยวกับงานวิจัยทั้งหมดที่นำมาวิเคราะห์]

## วิเคราะห์แต่ละงานวิจัย
[สร้าง subsection สำหรับแต่ละงานตามรูปแบบนี้]
### [ชื่องานวิจัย]
- **วัตถุประสงค์ / คำถามวิจัย:** [ระบุเป้าหมายหลักและคำถามที่งานวิจัยพยายามตอบ]
- **กรอบทฤษฎี / แนวคิดหลัก:** [ทฤษฎีหรือแนวคิดที่ใช้เป็นฐาน]
- **ระเบียบวิธีวิจัย:** [รูปแบบการวิจัย, กลุ่มตัวอย่าง/ชุดข้อมูล, เครื่องมือ, สถิติ/วิธีวิเคราะห์]
- **ผลการวิจัยสำคัญ:** [ผลลัพธ์หลักและข้อค้นพบที่มีนัยสำคัญ]
- **คุณค่าทางวิชาการ / ข้อค้นพบใหม่:** [การมีส่วนร่วมต่อองค์ความรู้และความเป็นต้นฉบับ]
- **ข้อจำกัดของงานวิจัย:** [ขอบเขต ข้อบกพร่องด้านระเบียบวิธี หรือความไม่ครบถ้วน]

## การเปรียบเทียบและสังเคราะห์
[ใช้ตาราง Markdown เปรียบเทียบในมิติสำคัญ เช่น ระเบียบวิธี กลุ่มตัวอย่าง ผลลัพธ์ ข้อสรุป ความสอดคล้องหรือขัดแย้ง]
| มิติ | งานวิจัย 1 | งานวิจัย 2 | ... |
|------|-----------|-----------|-----|
[หากมีงานวิจัยเดียว ให้ข้ามตารางและสรุปจุดเด่นเชิงเปรียบเทียบกับงานวิจัยทั่วไปในสาขา]

## ช่องว่างการวิจัยและข้อเสนอแนะ
[ประเด็นที่ยังไม่ได้ศึกษาหรือควรศึกษาต่อ ข้อเสนอแนะเชิงปฏิบัติสำหรับนักวิจัย]

กฎที่ต้องปฏิบัติอย่างเคร่งครัด:
- อิงเฉพาะข้อมูลที่ปรากฏในเนื้อหาที่ให้มาเท่านั้น ห้ามสร้างหรืออ้างอิงข้อมูลที่ไม่มีในข้อความ
- หากมิติใดไม่มีข้อมูลในเนื้อหาที่ให้มา ให้ระบุ "ไม่พบข้อมูลในส่วนนี้" อย่างชัดเจน
- ใช้ภาษาวิชาการที่ชัดเจนและกระชับ ตอบเป็นภาษาไทยเป็นหลัก คงศัพท์เทคนิคอังกฤษไว้ในวงเล็บ
- ตรวจสอบว่าทุกข้อสรุปมีหลักฐานรองรับจากเนื้อหาที่ให้มาก่อนส่งคำตอบ
- ห้ามกล่าวถึงชื่องานวิจัยที่ไม่ได้อยู่ในรายการที่ผู้ใช้เลือก
"""


def analyze_papers_critically_stream(
    papers_context: str,
    selected_paper_names: list,
) -> Generator[str, None, None]:
    """
    Critically analyze and compare research papers stored in the knowledge base.

    Args:
        papers_context: Assembled text from retrieved chunks, labelled per paper.
        selected_paper_names: Display names of the selected papers.

    Yields:
        str: Token strings from the streaming API response.
    """
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        raise ValueError(
            "❌ OPENTHAI_API_KEY not found. Please ensure:\n"
            f"  1. You have a .env file at: {env_path.resolve()}\n"
            "  2. OPENTHAI_API_KEY=your_key_here is set (no quotes, no spaces)"
        )

    paper_list_str = "\n".join(f"- {name}" for name in selected_paper_names)
    user_message = (
        f"กรุณาวิเคราะห์เชิงวิพากษ์งานวิจัยต่อไปนี้:\n{paper_list_str}\n\n"
        f"=== เนื้อหาจากแหล่งความรู้ ===\n{papers_context}"
    )
    messages = [
        {"role": "system", "content": PAPER_COMPARISON_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    yield from _call_api_stream(messages, api_key, max_tokens=6144, temperature=0.4)


ADVISOR_SYSTEM_PROMPT = """\
คุณคือ "อาจารย์ที่ปรึกษาวิจัย" เข้มงวด ประสบการณ์ 20+ ปี ตรวจงานระดับ ป.ตรี-เอก
ให้ feedback ชี้จุดแข็ง/จุดอ่อน น้ำเสียงเข้มงวดแต่ให้กำลังใจ

เกณฑ์ตรวจ (เฉพาะบทที่ปรากฏในงาน):
บท1: ความสำคัญของปัญหา, วัตถุประสงค์, สมมติฐาน, ขอบเขต, คำนิยาม
บท2: ทฤษฎี, งานวิจัยที่เกี่ยวข้อง, กรอบแนวคิด, การสังเคราะห์วรรณกรรม
บท3: รูปแบบวิจัย, กลุ่มตัวอย่าง, เครื่องมือ, การเก็บข้อมูล, สถิติ
บท4: การนำเสนอผล, ตาราง/แผนภูมิ, การแปลผล
บท5: สรุปผล, อภิปรายผล, ข้อเสนอแนะ

รูปแบบการตอบ — แต่ละข้อระบุ tag:
- [ต้องแก้ไข] ปัญหาร้ายแรง พร้อมวิธีแก้
- [ดีแล้ว] ส่วนที่ถูกต้องน่าชื่นชม
- [คำแนะนำ] ข้อเสนอแนะเพิ่มเติม

เริ่มด้วยสรุปภาพรวมสั้นๆ → review ทีละประเด็น → ปิดด้วยสรุปคะแนนภาพรวม

กฎต่อต้านการแต่งข้อมูล:
- ใช้เฉพาะข้อมูลที่ปรากฏจริงในงานที่ให้มาตรวจ
- ห้ามสร้างหรืออ้างอิงสถิติ ชื่อผู้แต่ง หรืองานวิจัยที่ไม่มีอยู่จริงในเนื้อหา
- หากบริบทจากเอกสารมีมาตรฐานวิชาการที่ดีกว่า ให้ใช้เป็นเกณฑ์เปรียบเทียบได้

ตรวจสอบภายในก่อนส่ง (ไม่ต้องแสดงในผลลัพธ์):
1. ทุก [ต้องแก้ไข] และ [คำแนะนำ] อ้างอิงเนื้อหาที่มีจริงในงานที่ส่งมาตรวจ ไม่ได้สร้างขึ้น
2. ไม่มีการอ้างชื่อผู้วิจัย สถิติ หรือทฤษฎีที่ไม่ปรากฏในงานหรือในมาตรฐานวิชาการที่ให้มา
3. ขอบเขตของ feedback ตรงกับบทที่ปรากฏในงาน ไม่ตรวจสอบบทที่ไม่ได้ส่งมา
"""

# ── Token safety constants for reviewer ──────────────────────────────────────
# Single-pass limit before chunked processing kicks in
_REVIEW_SINGLE_PASS_CHARS = 7000
# Hard cap per chunk when splitting
_REVIEW_CHUNK_CHARS = 6000
# Heading patterns used to split content into logical sections
_HEADING_PATTERN = re.compile(
    r'(?m)^(?:บทที่\s*\d+|Chapter\s+\d+|บทนำ|ทบทวนวรรณกรรม|วิธีดำเนินการ|'
    r'ผลการวิจัย|สรุป|Introduction|Literature Review|Methodology|Results?|'
    r'Discussion|Conclusion|Abstract|บทคัดย่อ)',
    re.IGNORECASE,
)


def _split_into_sections(text: str) -> list:
    """
    Split research content into logical sections by heading patterns.
    Returns a list of section strings. Falls back to fixed-size splitting
    if no headings are found.
    """
    boundaries = [m.start() for m in _HEADING_PATTERN.finditer(text)]
    if len(boundaries) < 2:
        # No headings found — split by fixed size
        return [text[i:i + _REVIEW_CHUNK_CHARS] for i in range(0, len(text), _REVIEW_CHUNK_CHARS)]
    sections = []
    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(text)
        sections.append(text[start:end])
    return sections


def review_research(content: str, user_focus: str = "",
                    retrieved_docs=None) -> tuple:
    """
    Review research content as a strict thesis advisor.

    For large content (>7000 chars) applies token-safe chunked processing:
    splits by section heading, reviews each chunk, then merges into a single
    structured feedback.  RAG context (if provided and relevant) is injected
    into the system prompt to ground the review against domain literature.

    Args:
        content: The research text from the workbench editor.
        user_focus: Optional user instruction about what to focus the review on.
        retrieved_docs: Optional RAG context documents from vector store.

    Returns:
        (review_text, input_tokens, output_tokens)
    """
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        raise ValueError(
            "❌ OPENTHAI_API_KEY not found. Please ensure:\n"
            f"  1. You have a .env file at: {env_path.resolve()}\n"
            "  2. OPENTHAI_API_KEY=your_key_here is set (no quotes, no spaces)"
        )

    total_input = 0
    total_output = 0

    # ── RAG context: relevance-filtered ──────────────────────────────────────
    _MAX_DOC_CHARS = 1200
    _MAX_CONTEXT_CHARS = 3500
    rag_context_text = ""
    rag_addition = ""
    if retrieved_docs:
        parts = [doc.page_content[:_MAX_DOC_CHARS] for doc in retrieved_docs]
        raw_context = "\n\n".join(parts)[:_MAX_CONTEXT_CHARS]
        # Relevance: check against content snippet + user focus
        check_text = content[:500] + " " + user_focus
        # Simple overlap check (same logic as generator._rag_relevance_score)
        import re as _re_inner
        def _tokens(t: str) -> set:
            return set(_re_inner.split(r'[\s\u0020-\u002F\u003A-\u0040]+', t.lower())) - {'', 'the', 'a', 'an', 'ที่', 'และ', 'ของ', 'ใน'}
        q_tok = _tokens(check_text)
        c_tok = _tokens(raw_context)
        relevance = min(1.0, len(q_tok & c_tok) / max(len(q_tok), 1))
        if relevance >= 0.12:
            rag_context_text = raw_context
            rag_addition = (
                "\n\n=== มาตรฐานวิชาการจากเอกสาร (ใช้เป็นเกณฑ์เปรียบเทียบ) ===\n"
                + rag_context_text
            )

    # ── Token safety: single-pass vs chunked ─────────────────────────────────
    if len(content) <= _REVIEW_SINGLE_PASS_CHARS:
        # Single pass — content is small enough
        user_parts = [f"=== งานวิจัยที่ต้อง Review ===\n{content}"]
        if user_focus and user_focus.strip():
            user_parts.append(
                f"=== สิ่งที่นักศึกษาอยากให้เน้น Review เป็นพิเศษ ===\n{user_focus.strip()}"
            )
        if rag_addition:
            user_parts.append(rag_addition)

        messages = [
            {"role": "system", "content": ADVISOR_SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]
        raw, ri, ro = _call_api(messages, api_key, max_tokens=4096, temperature=0.3)
        total_input += ri
        total_output += ro
        review_parts = [raw]

    else:
        # Chunked pass — split by sections then summarize if still too large
        sections = _split_into_sections(content)
        # If any individual section is still too large, summarize it first
        processed_sections = []
        for sec in sections:
            if len(sec) > _REVIEW_CHUNK_CHARS:
                # Summarize the oversized section before review
                sum_messages = [
                    {"role": "system", "content": (
                        "สรุปส่วนงานวิจัยนี้เป็น 3-5 ย่อหน้า รักษาประเด็นหลักทุกข้อ "
                        "ตอบเป็น plain text เท่านั้น"
                    )},
                    {"role": "user", "content": sec[:_REVIEW_CHUNK_CHARS]},
                ]
                sum_raw, sri, sro = _call_api(
                    sum_messages, api_key, max_tokens=1024, temperature=0.3
                )
                total_input += sri
                total_output += sro
                processed_sections.append(_THINK_RE.sub('', sum_raw).strip())
            else:
                processed_sections.append(sec)

        # Review each processed section
        review_parts = []
        focus_suffix = (
            f"\n=== สิ่งที่ต้องเน้น Review ===\n{user_focus.strip()}"
            if user_focus and user_focus.strip() else ""
        )
        for idx, sec_text in enumerate(processed_sections, 1):
            chunk_msg = (
                f"=== ส่วนที่ {idx}/{len(processed_sections)} ===\n{sec_text}"
                + focus_suffix
                + (rag_addition if rag_addition else "")
            )
            messages = [
                {"role": "system", "content": ADVISOR_SYSTEM_PROMPT},
                {"role": "user", "content": chunk_msg},
            ]
            raw, ri, ro = _call_api(messages, api_key, max_tokens=2048, temperature=0.3)
            total_input += ri
            total_output += ro
            review_parts.append(raw)

    # ── Merge results ─────────────────────────────────────────────────────────
    # Collect <think> blocks from all parts; strip from review body
    think_all = []
    review_bodies = []
    for part in review_parts:
        t_matches = re.findall(r'<think>(.*?)</think>', part, re.DOTALL)
        if t_matches:
            think_all.extend(t.strip() for t in t_matches)
        review_bodies.append(_THINK_RE.sub('', part).strip())

    think_text = '\n\n'.join(think_all) if think_all else ''
    review_text = '\n\n---\n\n'.join(b for b in review_bodies if b)

    # Re-attach think block at the top so app.py's parse_think_content() can surface it
    if think_text:
        review_text = f"<think>{think_text}</think>\n\n{review_text}"
    return review_text, total_input, total_output
