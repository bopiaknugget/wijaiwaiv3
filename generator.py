"""
Response Generation Module – Agentic Editor Edition
Handles AI-powered answer generation and editor manipulation using OpenThaiGPT API.

Improvements in this version:
- Query routing: is_small_talk() detects queries that don't need vector retrieval
- Streaming: _call_api_stream() yields tokens as they arrive (SSE)
- generate_answer_stream(): streaming variant of generate_answer for use with
  st.write_stream() in Streamlit
"""

import json
import os
import re
import textwrap
from typing import Generator, Optional
import requests
from pathlib import Path
from dotenv import load_dotenv

_THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL)
_PARAMETRIC_WARNING = (
    "⚠️ หมายเหตุ: ข้อมูลนี้มาจากฐานความรู้ทั่วไปของ AI "
    "เนื่องจากไม่พบข้อมูลในเอกสารหรือโน้ตของคุณ"
)

# ── Sentence Completion Guard ─────────────────────────────────────────────────
# Thai sentence endings: full stop, Thai characters followed by space/newline,
# common Thai particles that end sentences (ครับ, ค่ะ, นะ, etc.)
_THAI_SENTENCE_END = re.compile(
    r'[\.\!\?]'                       # universal punctuation
    r'|(?<=[ครับค่ะนะคะจ้ะจ๊ะด้วยเลย])\s'  # Thai particles followed by space
    r'|(?<=[ครับค่ะนะคะจ้ะจ๊ะด้วยเลย])$'    # Thai particles at end of text
    r'|\n\n'                          # paragraph break = safe boundary
)


def ensure_complete_sentence(text: str) -> str:
    """
    Trim text to the last complete sentence to prevent incomplete output.

    Handles both Thai and English text. If the text already ends with a
    sentence-ending character, it is returned unchanged. Otherwise it is
    trimmed back to the last sentence boundary found.

    Args:
        text: The raw LLM output text

    Returns:
        str: Text trimmed to the last complete sentence
    """
    if not text or not text.strip():
        return text

    text = text.rstrip()

    # Already ends cleanly
    if text[-1] in '.!?:' or text.endswith('ครับ') or text.endswith('ค่ะ') or text.endswith('นะ') or text.endswith('คะ'):
        return text

    # Find the last sentence-ending position
    # Strategy: search for the last occurrence of sentence-ending punctuation
    last_good = -1

    # Check for last period/exclamation/question that isn't inside a number
    for match in re.finditer(r'[\.\!\?](?:\s|$)', text):
        last_good = match.end()

    # Check for last paragraph break
    last_para = text.rfind('\n\n')
    if last_para > last_good:
        # Find end of the sentence after the paragraph break
        last_good = last_para

    # Check for Thai sentence-ending particles followed by space or end
    for match in re.finditer(r'(?:ครับ|ค่ะ|นะ|คะ|จ้ะ|จ๊ะ|เลย|ด้วย)(?:\s|$)', text):
        pos = match.end()
        if pos > last_good:
            last_good = pos

    if last_good > len(text) * 0.5:
        # Only trim if we keep at least 50% of the text
        return text[:last_good].rstrip()

    # If no good boundary found or it would cut too much, return as-is
    return text


API_URL = "http://thaillm.or.th/api/openthaigpt/v1/chat/completions"
MODEL = "/model"

# ── Edit Intent Detection (local, no API call) ───────────────────────────────
# Thai edit verbs + document-reference nouns used for lightweight pre-check
_EDIT_VERBS = re.compile(
    r'แก้ไข|เพิ่ม(?:เนื้อหา|ข้อมูล|บท)|เขียน|ลบ|แทรก|ปรับ|เปลี่ยน|'
    r'อัปเดต|แก้|เรียบเรียง|สรุป.*(?:ลง|ใส่|เขียน)|'
    r'\bedit\b|\badd\b|\bwrite\b|\bdelete\b|\binsert\b|\bmodify\b|\bupdate\b|\breplace\b',
    re.IGNORECASE,
)
_DOC_REFS = re.compile(
    r'เอกสาร|editor|ตัวแก้ไข|workbench|เนื้อหา|บทความ|ในเอกสาร|'
    r'บทนำ|บทที่|หัวข้อ|ย่อหน้า|document|content',
    re.IGNORECASE,
)


def is_edit_intent(query: str) -> bool:
    """
    Lightweight local check (regex/keyword) to determine if the user likely
    wants an edit action. Used by app.py to decide between streaming (chat)
    and non-streaming (edit) paths — no API call involved.

    Returns True if the query contains edit-intent verbs AND document references,
    OR if it contains strong edit-only patterns like "เขียนบทนำ", "เพิ่มเนื้อหาเกี่ยวกับ".
    """
    q = query.strip()
    # Strong patterns that are unambiguously edit commands
    if re.search(r'เขียน(?:บท|เนื้อหา|หัวข้อ)', q):
        return True
    if re.search(r'เพิ่มเนื้อหา|เพิ่มข้อมูล|แก้ไข(?:เนื้อหา|เอกสาร|หัวข้อ|ย่อหน้า)', q):
        return True
    if re.search(r'\b(?:edit|write|add|insert|modify)\s+(?:the\s+)?(?:document|content|section|editor)\b', q, re.IGNORECASE):
        return True
    # Weaker: verb + doc reference together
    if _EDIT_VERBS.search(q) and _DOC_REFS.search(q):
        return True
    return False

# ── Query Routing: small-talk patterns ────────────────────────────────────────
# Queries matching these patterns are answered directly from the LLM without
# touching the vector store.  This eliminates unnecessary Pinecone latency for
# conversational turns that carry no document-retrieval intent.

_SMALL_TALK_PATTERNS = re.compile(
    r"^("
    r"สวัสดี|หวัดดี|ดีครับ|ดีค่ะ|hello|hi\b|hey\b|"
    r"คุณชื่ออะไร|คุณเป็นใคร|คุณทำอะไรได้|ช่วยอะไรได้|"
    r"ขอบคุณ|thank(s|\s+you)|เยี่ยม|ดีมาก|โอเค|ok\b|okay\b|"
    r"ลาก่อน|bye\b|goodbye|"
    r"คุณเป็น ai|คุณเป็นหุ่นยนต์|are you (an? )?ai|"
    r"กี่โมง|วันนี้วันที่|today|what time"
    r")",
    re.IGNORECASE,
)


def is_small_talk(query: str) -> bool:
    """
    Return True if the query is conversational small talk that does not need
    vector database retrieval.

    The check is intentionally conservative: only obvious greetings and
    meta-questions about the assistant are flagged.  Any query mentioning
    research, documents, or content will pass through to retrieval normally.

    Args:
        query: The raw user input string

    Returns:
        bool: True if retrieval should be skipped
    """
    stripped = query.strip()
    if len(stripped) > 80:
        # Long queries almost certainly have research intent
        return False
    return bool(_SMALL_TALK_PATTERNS.match(stripped))


def _call_api(messages, api_key, max_tokens=2048, temperature=0.3):
    """
    Call the OpenThaiGPT API and return (content, input_tokens, output_tokens).
    """
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
    response = requests.post(API_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    return content, input_tokens, output_tokens


def _call_api_stream(messages, api_key, max_tokens=2048,
                     temperature=0.3) -> Generator[str, None, None]:
    """
    Call the OpenThaiGPT API with streaming enabled (Server-Sent Events).

    Yields individual token strings as they arrive.  Callers should accumulate
    the yielded strings to reconstruct the full response.

    Note: If the API does not support streaming or returns a non-streaming
    response, this function falls back gracefully by yielding the full content
    as a single chunk.

    Args:
        messages: OpenAI-format message list
        api_key: OPENTHAI_API_KEY string
        max_tokens: Maximum output tokens
        temperature: Sampling temperature

    Yields:
        str: Token strings (may be multi-character chunks)
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
    # OpenThaiGPT is strict about the JSON streaming flag. Send the body as
    # compact JSON so the wire payload contains `"stream":true`.
    payload_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    try:
        with requests.post(
            API_URL, headers=headers, data=payload_body,
            timeout=90, stream=True
        ) as response:
            response.raise_for_status()

            # Check if the server is actually streaming
            content_type = response.headers.get("Content-Type", "")
            if "text/event-stream" not in content_type and "stream" not in content_type:
                # Server returned a regular JSON response — yield it wholesale
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    yield content
                return

            # Parse SSE stream
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

    except requests.HTTPError as e:
        # Re-raise HTTP errors so callers can handle them
        raise
    except requests.RequestException as e:
        raise


def _extract_json(text: str):
    """
    Robustly extract the first JSON object from potentially mixed LLM output.
    Handles markdown code fences and conversational wrapper text.
    Returns a dict or None.
    """
    text = text.strip()
    # Strip markdown code fences (```json ... ``` or ``` ... ```)
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```\s*$', '', text, flags=re.MULTILINE)
    text = text.strip()

    # Try direct parse first (ideal case — LLM returned only JSON)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Scan for a JSON object using balanced-brace matching
    # This correctly handles nested strings containing braces
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break
    return None


def generate_answer(query, retrieved_docs, chat_history=None, editor_content=None,
                    research_mode=False, **kwargs):
    """
    Generate an AI-powered answer or editor action using OpenThaiGPT API.

    The LLM classifies user intent as "chat" (Q&A) or "edit" (editor manipulation)
    and returns a structured JSON response. When no RAG context is available,
    the LLM may use parametric knowledge and a warning is prepended to the response.

    Args:
        query (str): The user's message / instruction
        retrieved_docs (list): Retrieved Document objects from vector store (may be empty)
        chat_history (list, optional): Previous messages for context re-phrasing
        editor_content (str, optional): Current content of the work editor
        research_mode (bool): When True, use research-optimised prompt with higher token budget

    Returns:
        tuple: (action, response_text, new_editor_content, input_tokens, output_tokens)
            action (str): "chat", "edit", or "research"
            response_text (str): Message to display in chat
            new_editor_content (str | None): New editor text when action="edit"/"research", else None
            input_tokens (int): Cumulative API input token count
            output_tokens (int): Cumulative API output token count
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

    # ── Step 1: Build main generation prompt ──────────────────────────────────
    has_context = bool(retrieved_docs)
    # Cap each doc at 1,500 chars and total context at 6,000 chars (~1,500 tokens)
    # to stay well inside the 16K context window and keep LLM latency low
    _MAX_DOC_CHARS = 1500
    _MAX_CONTEXT_CHARS = 6000
    if retrieved_docs:
        parts = [doc.page_content[:_MAX_DOC_CHARS] for doc in retrieved_docs]
        context_text = "\n\n".join(parts)[:_MAX_CONTEXT_CHARS]
    else:
        context_text = ""

    # ── Language instruction ───────────────────────────────────────────────────
    language_instruction = (
        "ภาษา: ตอบภาษาเดียวกับคำถาม (default ไทย), technical terms ใช้อังกฤษได้"
    )

    if research_mode:
        # ── Research mode: deep analysis → JSON with editor_content ──────────
        if has_context:
            knowledge_instruction = "ใช้บริบทจากเอกสารเป็นหลักในการวิเคราะห์และสังเคราะห์"
        else:
            knowledge_instruction = "ไม่มีเอกสารถูกโหลดไว้ — ใช้ความรู้ทั่วไปของ AI"

        system_prompt = (
            "คุณเป็นนักวิจัยผู้เชี่ยวชาญ ค้นคว้า วิเคราะห์ และสังเคราะห์ข้อมูลเชิงลึก\n"
            f"{knowledge_instruction}\n"
            f"{language_instruction}\n\n"
            "กฎการเขียน:\n"
            "- เนื้อหา ≥1,000 คำ, ≥10 ย่อหน้า, แต่ละย่อหน้า ≥80 คำ พร้อมตัวอย่างและการวิเคราะห์\n"
            "- โครงสร้าง: บทนำ → ทบทวนวรรณกรรม → วิเคราะห์เชิงลึก → ผลการศึกษา → สรุป/ข้อเสนอแนะ\n\n"
            "ตอบ JSON เท่านั้น:\n"
            "{\"action\":\"research\",\"response\":\"สรุป 1-2 ประโยค\",\"editor_content\":\"เนื้อหาฉบับเต็ม ≥1,000 คำ\"}"
        )
    else:
        # ── Chat mode: Q&A with editor awareness + edit intent support ───────
        if has_context:
            knowledge_instruction = (
                "ใช้บริบทจากเอกสารด้านล่างเป็นหลักในการตอบ "
                "หากบริบทไม่เพียงพอ ให้ขึ้นต้นคำตอบด้วย "
                "\"⚠️ หมายเหตุ: ข้อมูลนี้มาจากความรู้ทั่วไปของ AI "
                "เนื่องจากไม่พบข้อมูลในเอกสารหรือโน้ตของคุณ\""
            )
        else:
            knowledge_instruction = (
                "ไม่มีเอกสารถูกโหลดไว้ — ตอบจากความรู้ทั่วไปของ AI ได้เลย"
            )

        # Determine if this call expects edit capability (set by app.py)
        _edit_capable = kwargs.get("edit_capable", False)

        if _edit_capable:
            edit_instruction = (
                "\n\nการจำแนกเจตนา:\n"
                "- หากผู้ใช้ขอแก้ไข/เพิ่ม/เขียน/ลบ/แทรก/ปรับเนื้อหาในเอกสาร → "
                "ตอบ JSON: {\"action\":\"edit\",\"response\":\"สรุปสิ่งที่ทำ\",\"editor_content\":\"เนื้อหาใหม่ทั้งหมดของเอกสาร\"}\n"
                "- editor_content ต้องเป็นเนื้อหาฉบับเต็มของเอกสาร (รวมส่วนที่ไม่ได้แก้ด้วย)\n"
                "- หากผู้ใช้ถามคำถามหรือสนทนา → ตอบเป็น plain text ธรรมดา ไม่ต้องมี JSON"
            )
        else:
            edit_instruction = ""

        editor_awareness = ""
        if editor_content and editor_content.strip():
            editor_awareness = (
                "\nคุณรับรู้เนื้อหาในตัวแก้ไขของผู้ใช้ สามารถตอบคำถามเกี่ยวกับเนื้อหานั้นได้"
            )

        system_prompt = (
            "คุณเป็นผู้ช่วยวิจัย ทำหน้าที่ตอบคำถามอย่างตรงประเด็นและถูกต้อง\n"
            f"{knowledge_instruction}\n"
            f"{language_instruction}"
            f"{editor_awareness}\n\n"
            "แนวทางการตอบ:\n"
            "- ตอบตรงคำถาม ชัดเจน ครบถ้วน\n"
            "- อ้างอิงเนื้อหาจากเอกสารเมื่อเกี่ยวข้อง\n"
            "- ตอบเป็น plain text ธรรมดา (เว้นแต่ได้รับคำสั่งแก้ไขเอกสาร)"
            f"{edit_instruction}"
        )

    user_parts = [f"คำถาม: {query}"]
    if context_text:
        user_parts.append(f"=== บริบทจากเอกสาร ===\n{context_text}")
    # Include editor content for both research and chat modes (different limits)
    if editor_content and editor_content.strip():
        if research_mode:
            editor_tail = editor_content.strip()[-1500:]
        else:
            # Chat mode: smaller budget (800 chars) to save ~300 tokens
            editor_tail = editor_content.strip()[-800:]
        user_parts.append(f"=== เนื้อหาในตัวแก้ไขปัจจุบัน (ส่วนท้าย) ===\n{editor_tail}")

    # Build messages: system + chat history (last 4, each capped at 400 chars) + current user message
    # Capping history prevents long research-mode answers from bloating the context window
    messages = [{"role": "system", "content": system_prompt}]
    if chat_history:
        for msg in chat_history[-4:]:
            role = msg.get("role", "user")
            content = _THINK_RE.sub('', msg.get("content", "")).strip()[:400]
            if content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": "\n\n".join(user_parts)})

    # ── Step 2: Call API ───────────────────────────────────────────────────────
    # OpenThaiGPT context window = 16,384 tokens (input + output).
    # Reserve headroom for input tokens to avoid 400 errors.
    _edit_capable = kwargs.get("edit_capable", False)
    if research_mode:
        api_max_tokens = 8192
    elif _edit_capable:
        api_max_tokens = 4096  # edit needs room for full editor content
    else:
        api_max_tokens = 2048
    api_temperature = 0.65 if research_mode else 0.3
    try:
        raw, ri, ro = _call_api(messages, api_key, max_tokens=api_max_tokens, temperature=api_temperature)
        total_input += ri
        total_output += ro
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status in (401, 403):
            raise ValueError(f"❌ API key invalid or unauthorized (HTTP {status}).")
        elif status == 429:
            raise ValueError("❌ API quota exceeded (429). รอสักครู่แล้วลองใหม่")
        else:
            raise ValueError(f"❌ HTTP error {status}: {e}")
    except requests.RequestException as e:
        raise ValueError(f"❌ Request failed: {e}")

    # ── Step 3: Parse response ─────────────────────────────────────────────────
    think_matches = re.findall(r'<think>.*?</think>', raw, re.DOTALL)
    think_prefix = '\n'.join(think_matches).strip()
    raw_clean = _THINK_RE.sub('', raw).strip()

    # ── Chat / Edit mode parsing ─────────────────────────────────────────────
    if not research_mode:
        # Check if LLM returned an edit-action JSON (only when edit_capable)
        if _edit_capable:
            parsed_edit = _extract_json(raw_clean)
            if parsed_edit and parsed_edit.get("action") == "edit":
                response_text = str(parsed_edit.get("response", "")).strip()
                new_editor = parsed_edit.get("editor_content")
                if isinstance(new_editor, str):
                    new_editor = ensure_complete_sentence(new_editor.strip()) or None
                else:
                    new_editor = None
                if think_prefix:
                    response_text = (think_prefix + "\n\n" + response_text).strip()
                if new_editor:
                    return "edit", response_text, new_editor, total_input, total_output
                # Fallback: JSON had action=edit but no editor_content → treat as chat

        response_text = ensure_complete_sentence(raw_clean)
        if not has_context and not response_text.startswith("⚠️"):
            response_text = _PARAMETRIC_WARNING + "\n\n" + response_text
        if think_prefix:
            response_text = (think_prefix + "\n\n" + response_text).strip()
        return "chat", response_text, None, total_input, total_output

    # ── Research mode: parse JSON → push editor_content to editor ────────────
    parsed = _extract_json(raw_clean)
    if parsed is None:
        # JSON parse failed — try regex extraction of editor_content
        editor_match = re.search(
            r'"editor_content"\s*:\s*"((?:[^"\\]|\\.)*)"',
            raw_clean, re.DOTALL
        )
        if editor_match:
            editor_text = editor_match.group(1)
            try:
                editor_text = json.loads(f'"{editor_text}"')
            except (json.JSONDecodeError, ValueError):
                pass
        else:
            editor_text = raw_clean  # fallback: full text to editor

        response_text = "🔬 ผลการค้นคว้าถูกส่งไปยัง Research Workbench แล้ว"
        if think_prefix:
            response_text = (think_prefix + "\n\n" + response_text).strip()
        return "research", response_text, editor_text, total_input, total_output

    response_text = str(parsed.get("response", "")).strip()
    if think_prefix:
        response_text = (think_prefix + "\n\n" + response_text).strip()

    new_editor = parsed.get("editor_content")
    if isinstance(new_editor, str):
        new_editor = ensure_complete_sentence(new_editor.strip()) or None
    else:
        new_editor = None

    # Safety net: LLM forgot editor_content → use response text
    if not new_editor and response_text:
        new_editor = _THINK_RE.sub('', response_text).strip() or None

    # Must have content to be a research action
    if not new_editor:
        return "chat", response_text, None, total_input, total_output

    return "research", response_text, new_editor, total_input, total_output


def _rag_relevance_score(query: str, context_text: str) -> float:
    """
    Lightweight relevance check: returns a score 0.0–1.0 estimating how
    semantically related the RAG context is to the query.

    Uses token overlap (Thai-safe: split on whitespace + punctuation) so no
    external library is required.  A score below 0.15 is treated as LOW
    relevance and the caller should fall back to model knowledge.
    """
    if not query or not context_text:
        return 0.0
    import re as _re
    def _tokens(text: str) -> set:
        return set(_re.split(r'[\s\u0020-\u002F\u003A-\u0040\u005B-\u0060\u007B-\u007E]+', text.lower()))
    q_tokens = _tokens(query) - {'', 'the', 'a', 'an', 'is', 'in', 'of', 'and', 'or', 'ที่', 'และ', 'ของ', 'ใน'}
    c_tokens = _tokens(context_text)
    if not q_tokens:
        return 0.0
    overlap = len(q_tokens & c_tokens)
    return min(1.0, overlap / len(q_tokens))


def generate_section(topic, section_instruction, retrieved_docs=None,
                     existing_content=""):
    """
    Generate a single section of long-form content to APPEND to the editor.
    Used by the Section-by-Section mode for building documents incrementally.

    Args:
        topic: The overall document topic
        section_instruction: What to write in this section
        retrieved_docs: Optional RAG context documents from vector store
        existing_content: Current editor content (for continuity)

    Returns:
        tuple: (think_text, section_text, input_tokens, output_tokens)
    """
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        raise ValueError("OPENTHAI_API_KEY not found")

    # ── RAG context: relevance-filtered ──────────────────────────────────────
    _MAX_DOC_CHARS = 1500
    _MAX_CONTEXT_CHARS = 6000
    context_text = ""
    rag_instruction = ""
    if retrieved_docs:
        parts = [doc.page_content[:_MAX_DOC_CHARS] for doc in retrieved_docs]
        raw_context = "\n\n".join(parts)[:_MAX_CONTEXT_CHARS]
        relevance = _rag_relevance_score(f"{topic} {section_instruction}", raw_context)
        if relevance >= 0.15:
            context_text = raw_context
            rag_instruction = (
                "- ใช้บริบทจากเอกสารที่ให้มาเป็นข้อมูลอ้างอิงหลัก\n"
                "- ห้ามแต่งหรือสร้างข้อมูล สถิติ หรือการอ้างอิงที่ไม่ปรากฏในเอกสาร\n"
                "- หากไม่แน่ใจว่าข้อมูลมาจากเอกสาร ให้ระบุว่า 'ตามความรู้ทั่วไป' แทนการอ้างอิงเอกสาร\n"
            )
        # else: RAG not relevant — fall back to model knowledge, no rag_instruction

    # ── Token safety: cap existing_content tail ───────────────────────────────
    # Budget: ~6000 chars system + context (≈1500 tok) + 1500 existing + instruction
    # This stays well under the 16K context window.
    existing_tail = ""
    if existing_content and existing_content.strip():
        existing_tail = existing_content.strip()[-1500:]

    # ── No-RAG fallback signal ────────────────────────────────────────────────
    no_rag_notice = (
        "" if context_text else
        "- ไม่มีเอกสารอ้างอิงให้ใช้งาน — ให้ใช้ความรู้ทั่วไปของ AI "
        "และระบุ [ความรู้ทั่วไป] ไว้ท้ายย่อหน้าที่ไม่ได้อ้างอิงเอกสาร\n"
    )

    system_prompt = (
        "คุณเป็นนักเขียนวิชาการผู้เชี่ยวชาญ เขียนเนื้อหาทีละ section\n"
        "ภาษา: ตอบภาษาเดียวกับคำสั่ง (default ไทย), technical terms ใช้อังกฤษได้\n\n"
        "กฎการเขียน:\n"
        "- เขียนเฉพาะส่วนที่ได้รับมอบหมาย ห้ามเขียนส่วนอื่นหรือซ้ำกับเนื้อหาเดิม\n"
        "- ความยาวขั้นต่ำ 400 คำ, ≥4 ย่อหน้า — ขยายความละเอียด ยกตัวอย่าง อ้างทฤษฎี อธิบายกลไก\n"
        "- ตอบเป็น plain text เท่านั้น ห้าม JSON ห้ามคำอธิบายเพิ่ม\n"
        + rag_instruction
        + no_rag_notice +
        "- ห้ามสร้างการอ้างอิง ชื่อผู้แต่ง หรือสถิติที่ไม่มีหลักฐาน\n\n"
        "ก่อนส่งผลลัพธ์ ให้ตรวจสอบภายในดังนี้ (ไม่ต้องแสดงในผลลัพธ์):\n"
        "1. ข้อความทุกข้อที่อ้างอิงข้อมูลจำเพาะ (ตัวเลข สถิติ ชื่อผู้วิจัย) "
        "มาจากเอกสารที่ให้มาหรือเป็นความรู้ที่ตรวจสอบได้ — ถ้าไม่แน่ใจให้ลบออกหรือใช้ภาษาทั่วไปแทน\n"
        "2. เนื้อหาตรงตามส่วนที่ได้รับมอบหมายเท่านั้น ไม่เพิ่มส่วนอื่นโดยไม่ได้รับคำสั่ง\n"
        "3. ไม่มีการสร้างการอ้างอิง ชื่อผู้แต่ง หรือปีพิมพ์ที่ไม่ปรากฏในเอกสาร"
    )

    user_parts = [f"หัวข้อเอกสาร: {topic}", f"ส่วนที่ต้องเขียน: {section_instruction}"]
    if context_text:
        user_parts.append(f"=== บริบทจากเอกสาร (ใช้เป็นข้อมูลอ้างอิง) ===\n{context_text}")
    if existing_tail:
        user_parts.append(f"=== เนื้อหาที่เขียนไปแล้ว (ส่วนท้าย) ===\n{existing_tail}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    content, input_tokens, output_tokens = _call_api(
        messages, api_key, max_tokens=4096, temperature=0.65
    )
    think_matches = re.findall(r'<think>(.*?)</think>', content, re.DOTALL)
    think_text = '\n\n'.join(t.strip() for t in think_matches) if think_matches else ''
    content = _THINK_RE.sub('', content).strip()
    return think_text, content, input_tokens, output_tokens


def generate_section_from_docs(topic, section_instruction, retrieved_docs=None,
                               existing_content=""):
    """
    Generate a single section of long-form content grounded strictly in provided documents.
    Used by the Section-by-Section mode when the user selects "เอกสารที่เลือก" as the source.

    Unlike generate_section(), this function:
    - Requires all factual claims to be traceable to the provided documents
    - Labels non-document information with [ความรู้ทั่วไป]
    - Emits a clear warning if no relevant document content is found (no hallucination)
    - Uses a lower temperature (0.45) for higher faithfulness

    Args:
        topic: The overall document topic
        section_instruction: What to write in this section
        retrieved_docs: RAG context documents retrieved from vector store (required)
        existing_content: Current editor content (for continuity)

    Returns:
        tuple: (think_text, section_text, input_tokens, output_tokens)
    """
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        raise ValueError("OPENTHAI_API_KEY not found")

    # ── RAG context: relevance-filtered ──────────────────────────────────────
    _MAX_DOC_CHARS = 2000
    _MAX_CONTEXT_CHARS = 8000
    context_text = ""
    has_relevant_docs = False
    if retrieved_docs:
        parts = [doc.page_content[:_MAX_DOC_CHARS] for doc in retrieved_docs]
        raw_context = "\n\n".join(parts)[:_MAX_CONTEXT_CHARS]
        relevance = _rag_relevance_score(f"{topic} {section_instruction}", raw_context)
        if relevance >= 0.15:
            context_text = raw_context
            has_relevant_docs = True

    # ── Token safety: cap existing_content tail ───────────────────────────────
    existing_tail = ""
    if existing_content and existing_content.strip():
        existing_tail = existing_content.strip()[-1500:]

    # ── Build system prompt ───────────────────────────────────────────────────
    if has_relevant_docs:
        grounding_rules = (
            "- ข้อมูลทุกข้อต้องอ้างอิงจากเอกสารที่ให้มาเท่านั้น\n"
            "- หากต้องการเพิ่มข้อมูลทั่วไปที่ไม่มีในเอกสาร ให้ระบุ [ความรู้ทั่วไป] ไว้หน้าข้อความนั้น\n"
            "- ห้ามแต่งหรือสร้างข้อมูล สถิติ ชื่อผู้วิจัย หรือการอ้างอิงที่ไม่ปรากฏในเอกสาร\n"
            "- ห้ามใส่การอ้างอิง (citation) ที่ไม่มีในเอกสารที่ให้มา\n"
            "- เขียนให้ครอบคลุมเนื้อหาสำคัญจากเอกสารที่ให้มา\n"
        )
        no_docs_warning = ""
    else:
        grounding_rules = (
            "- ไม่พบเอกสารที่เกี่ยวข้องกับหัวข้อนี้ในฐานข้อมูล\n"
            "- ให้เริ่มต้นเนื้อหาด้วยคำเตือนนี้: "
            "\"[คำเตือน: ไม่พบเอกสารอ้างอิงที่เกี่ยวข้อง เนื้อหาต่อไปนี้อาศัยความรู้ทั่วไปของ AI]\"\n"
            "- ทุกย่อหน้าต้องมีป้ายกำกับ [ความรู้ทั่วไป] นำหน้า\n"
            "- ห้ามแต่งข้อมูล สถิติ หรือการอ้างอิงที่ไม่มีหลักฐาน\n"
        )
        no_docs_warning = ""

    system_prompt = (
        "คุณเป็นนักเขียนวิชาการที่เน้นความถูกต้องและการอ้างอิงแหล่งที่มา เขียนเนื้อหาทีละ section\n"
        "ภาษา: เขียนภาษาไทย ยกเว้นเอกสารที่ให้มาเป็นภาษาอังกฤษ ให้เขียนภาษาอังกฤษได้ "
        "— technical terms ใช้อังกฤษได้เสมอ\n\n"
        "กฎการเขียน:\n"
        "- เขียนเฉพาะส่วนที่ได้รับมอบหมาย ห้ามเขียนส่วนอื่นหรือซ้ำกับเนื้อหาเดิม\n"
        "- ความยาวขั้นต่ำ 400 คำ, ≥4 ย่อหน้า — ขยายความละเอียดจากเอกสารที่ให้มา\n"
        "- ตอบเป็น plain text เท่านั้น ห้าม JSON ห้ามคำอธิบายเพิ่ม\n"
        + grounding_rules +
        "\nก่อนส่งผลลัพธ์ ให้ตรวจสอบภายในดังนี้ (ไม่ต้องแสดงในผลลัพธ์):\n"
        "1. ข้อเท็จจริงทุกข้อ (ตัวเลข สถิติ ชื่อผู้วิจัย) มาจากเอกสารที่ให้มา — ถ้าไม่มีหลักฐาน ให้ใช้ [ความรู้ทั่วไป] หรือลบออก\n"
        "2. เนื้อหาตรงตามส่วนที่ได้รับมอบหมายเท่านั้น\n"
        "3. ไม่สร้างการอ้างอิง ชื่อผู้แต่ง หรือปีพิมพ์ที่ไม่ปรากฏในเอกสาร"
    )

    user_parts = [f"หัวข้อเอกสาร: {topic}", f"ส่วนที่ต้องเขียน: {section_instruction}"]
    if context_text:
        user_parts.append(f"=== เนื้อหาจากเอกสารที่เลือก (ใช้เป็นฐานข้อมูลหลัก) ===\n{context_text}")
    if existing_tail:
        user_parts.append(f"=== เนื้อหาที่เขียนไปแล้ว (ส่วนท้าย — ห้ามซ้ำ) ===\n{existing_tail}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    content, input_tokens, output_tokens = _call_api(
        messages, api_key, max_tokens=4096, temperature=0.45
    )
    think_matches = re.findall(r'<think>(.*?)</think>', content, re.DOTALL)
    think_text = '\n\n'.join(t.strip() for t in think_matches) if think_matches else ''
    content = _THINK_RE.sub('', content).strip()
    return think_text, content, input_tokens, output_tokens


def generate_section_stream(
    topic: str,
    section_instruction: str,
    existing_content: str = "",
) -> Generator[str, None, None]:
    """
    Streaming version of generate_section() for use with st.write_stream().
    Uses pure AI knowledge — no RAG retrieval, no document context.

    Yields raw token strings as they arrive from the API.
    """
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        raise ValueError("OPENTHAI_API_KEY not found")

    existing_tail = existing_content.strip()[-1500:] if existing_content and existing_content.strip() else ""

    system_prompt = (
        "คุณเป็นนักเขียนวิชาการผู้เชี่ยวชาญ เขียนเนื้อหาทีละ section\n"
        "ภาษา: ตอบภาษาเดียวกับคำสั่ง (default ไทย), technical terms ใช้อังกฤษได้\n\n"
        "กฎการเขียน:\n"
        "- เขียนเฉพาะส่วนที่ได้รับมอบหมาย ห้ามเขียนส่วนอื่นหรือซ้ำกับเนื้อหาเดิม\n"
        "- ความยาวขั้นต่ำ 400 คำ, ≥4 ย่อหน้า — ขยายความละเอียด ยกตัวอย่าง อ้างทฤษฎี อธิบายกลไก\n"
        "- ตอบเป็น plain text เท่านั้น ห้าม JSON ห้ามคำอธิบายเพิ่ม\n"
        "- ใช้ความรู้ทั่วไปของ AI — ห้ามสร้างการอ้างอิง ชื่อผู้แต่ง หรือสถิติที่ไม่มีหลักฐาน\n"
        "- ไม่ต้องระบุแหล่งที่มาหรือเชิงอรรถใดๆ\n\n"
        "ก่อนส่งผลลัพธ์ ให้ตรวจสอบภายในดังนี้ (ไม่ต้องแสดงในผลลัพธ์):\n"
        "1. เนื้อหาตรงตามส่วนที่ได้รับมอบหมายเท่านั้น\n"
        "2. ไม่มีการสร้างการอ้างอิง ชื่อผู้แต่ง หรือสถิติที่ไม่แน่ใจ"
    )

    user_parts = [f"หัวข้อเอกสาร: {topic}", f"ส่วนที่ต้องเขียน: {section_instruction}"]
    if existing_tail:
        user_parts.append(f"=== เนื้อหาที่เขียนไปแล้ว (ส่วนท้าย) ===\n{existing_tail}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    yield from _call_api_stream(messages, api_key, max_tokens=4096, temperature=0.65)


def generate_section_from_docs_stream(
    topic: str,
    section_instruction: str,
    retrieved_docs: list,
    existing_content: str = "",
) -> Generator[str, None, None]:
    """
    Streaming version of generate_section_from_docs() for use with st.write_stream().
    Grounds the output strictly in the provided retrieved documents.

    Yields raw token strings as they arrive from the API.
    """
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        raise ValueError("OPENTHAI_API_KEY not found")

    _MAX_DOC_CHARS = 2000
    _MAX_CONTEXT_CHARS = 8000
    context_text = ""
    has_relevant_docs = False
    if retrieved_docs:
        parts = [doc.page_content[:_MAX_DOC_CHARS] for doc in retrieved_docs]
        raw_context = "\n\n".join(parts)[:_MAX_CONTEXT_CHARS]
        relevance = _rag_relevance_score(f"{topic} {section_instruction}", raw_context)
        if relevance >= 0.15:
            context_text = raw_context
            has_relevant_docs = True

    existing_tail = existing_content.strip()[-1500:] if existing_content and existing_content.strip() else ""

    if has_relevant_docs:
        grounding_rules = (
            "- ข้อมูลทุกข้อต้องอ้างอิงจากเอกสารที่ให้มาเท่านั้น\n"
            "- หากต้องการเพิ่มข้อมูลทั่วไปที่ไม่มีในเอกสาร ให้ระบุ [ความรู้ทั่วไป] ไว้หน้าข้อความนั้น\n"
            "- ห้ามแต่งหรือสร้างข้อมูล สถิติ ชื่อผู้วิจัย หรือการอ้างอิงที่ไม่ปรากฏในเอกสาร\n"
            "- ห้ามใส่การอ้างอิง (citation) ที่ไม่มีในเอกสารที่ให้มา\n"
            "- เขียนให้ครอบคลุมเนื้อหาสำคัญจากเอกสารที่ให้มา\n"
        )
    else:
        grounding_rules = (
            "- ไม่พบเอกสารที่เกี่ยวข้องกับหัวข้อนี้ในฐานข้อมูล\n"
            "- ให้เริ่มต้นเนื้อหาด้วยคำเตือนนี้: "
            "\"[คำเตือน: ไม่พบเอกสารอ้างอิงที่เกี่ยวข้อง เนื้อหาต่อไปนี้อาศัยความรู้ทั่วไปของ AI]\"\n"
            "- ทุกย่อหน้าต้องมีป้ายกำกับ [ความรู้ทั่วไป] นำหน้า\n"
            "- ห้ามแต่งข้อมูล สถิติ หรือการอ้างอิงที่ไม่มีหลักฐาน\n"
        )

    system_prompt = (
        "คุณเป็นนักเขียนวิชาการที่เน้นความถูกต้องและการอ้างอิงแหล่งที่มา เขียนเนื้อหาทีละ section\n"
        "ภาษา: เขียนภาษาไทย ยกเว้นเอกสารที่ให้มาเป็นภาษาอังกฤษ ให้เขียนภาษาอังกฤษได้ "
        "— technical terms ใช้อังกฤษได้เสมอ\n\n"
        "กฎการเขียน:\n"
        "- เขียนเฉพาะส่วนที่ได้รับมอบหมาย ห้ามเขียนส่วนอื่นหรือซ้ำกับเนื้อหาเดิม\n"
        "- ความยาวขั้นต่ำ 400 คำ, ≥4 ย่อหน้า — ขยายความละเอียดจากเอกสารที่ให้มา\n"
        "- ตอบเป็น plain text เท่านั้น ห้าม JSON ห้ามคำอธิบายเพิ่ม\n"
        + grounding_rules +
        "\nก่อนส่งผลลัพธ์ ให้ตรวจสอบภายในดังนี้ (ไม่ต้องแสดงในผลลัพธ์):\n"
        "1. ข้อเท็จจริงทุกข้อ (ตัวเลข สถิติ ชื่อผู้วิจัย) มาจากเอกสารที่ให้มา — ถ้าไม่มีหลักฐาน ให้ใช้ [ความรู้ทั่วไป] หรือลบออก\n"
        "2. เนื้อหาตรงตามส่วนที่ได้รับมอบหมายเท่านั้น\n"
        "3. ไม่สร้างการอ้างอิง ชื่อผู้แต่ง หรือปีพิมพ์ที่ไม่ปรากฏในเอกสาร"
    )

    user_parts = [f"หัวข้อเอกสาร: {topic}", f"ส่วนที่ต้องเขียน: {section_instruction}"]
    if context_text:
        user_parts.append(f"=== เนื้อหาจากเอกสารที่เลือก (ใช้เป็นฐานข้อมูลหลัก) ===\n{context_text}")
    if existing_tail:
        user_parts.append(f"=== เนื้อหาที่เขียนไปแล้ว (ส่วนท้าย — ห้ามซ้ำ) ===\n{existing_tail}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    yield from _call_api_stream(messages, api_key, max_tokens=4096, temperature=0.45)


def generate_selection_edit(selected_text, instruction, retrieved_docs=None):
    """
    Edit a selected piece of text according to user instruction.

    Args:
        selected_text: The text the user has selected in the editor
        instruction: The edit instruction from the user
        retrieved_docs: Optional RAG context documents from vector store

    Returns:
        (think_text, edited_text, input_tokens, output_tokens)
    """
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        raise ValueError("OPENTHAI_API_KEY not found")

    # ── RAG context: relevance-filtered ──────────────────────────────────────
    _MAX_DOC_CHARS = 1200
    _MAX_CONTEXT_CHARS = 4000
    context_text = ""
    rag_instruction = ""
    if retrieved_docs:
        parts = [doc.page_content[:_MAX_DOC_CHARS] for doc in retrieved_docs]
        raw_context = "\n\n".join(parts)[:_MAX_CONTEXT_CHARS]
        # Check relevance against the instruction + selected text
        relevance = _rag_relevance_score(f"{instruction} {selected_text[:200]}", raw_context)
        if relevance >= 0.15:
            context_text = raw_context
            rag_instruction = (
                "\n- ใช้ข้อมูลจากบริบทเอกสารที่ให้มาเพื่อเสริมความถูกต้องหากเกี่ยวข้อง"
                "\n- ห้ามแต่งข้อมูล สถิติ หรือการอ้างอิงที่ไม่ปรากฏในเอกสาร"
            )

    # ── No-RAG fallback signal ────────────────────────────────────────────────
    no_rag_notice_edit = (
        "" if context_text else
        "\nไม่มีเอกสารอ้างอิง — แก้ไขจากข้อความเดิมและความรู้ทั่วไปเท่านั้น "
        "ห้ามเพิ่มข้อมูลที่ไม่มีในข้อความที่เลือก"
    )

    system_prompt = (
        "คุณเป็นผู้ช่วยแก้ไขข้อความ ผู้ใช้จะส่งข้อความที่เลือกไว้ "
        "พร้อมคำสั่งว่าต้องการแก้ไขอย่างไร\n"
        "ตอบเฉพาะข้อความที่แก้ไขแล้วเท่านั้น ห้ามมีคำอธิบายหรือข้อความเพิ่มเติม "
        "ห้ามใส่เครื่องหมายคำพูดครอบข้อความ\n"
        "แก้ไขโดยรักษาภาษาเดิมของข้อความ หากคำสั่งเป็นภาษาไทยให้ตอบเป็นภาษาไทย\n"
        "ห้ามสร้างข้อมูล ชื่อ หรือตัวเลขที่ไม่มีในข้อความเดิมหรือในบริบทเอกสาร"
        + rag_instruction
        + no_rag_notice_edit + "\n\n"
        "ตรวจสอบภายในก่อนส่ง (ไม่ต้องแสดงในผลลัพธ์):\n"
        "1. ผลลัพธ์ตรงตามคำสั่งแก้ไขเท่านั้น ไม่เปลี่ยนส่วนที่ไม่ได้รับคำสั่ง\n"
        "2. ไม่มีข้อมูลจำเพาะ (ตัวเลข ชื่อ สถิติ) ที่ไม่ปรากฏในข้อความเดิมหรือในเอกสาร\n"
        "3. ภาษาและโทนเสียงสอดคล้องกับข้อความเดิม"
    )
    _MAX_SELECTION_CHARS = 4000
    selected_text = selected_text[:_MAX_SELECTION_CHARS]

    user_parts = [
        f"ข้อความที่เลือก:\n\"\"\"\n{selected_text}\n\"\"\"",
        f"คำสั่งแก้ไข: {instruction}",
    ]
    if context_text:
        user_parts.append(f"=== บริบทจากเอกสาร (ใช้อ้างอิงหากเกี่ยวข้อง) ===\n{context_text}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    content, input_tokens, output_tokens = _call_api(
        messages, api_key, max_tokens=2048, temperature=0.3
    )
    think_matches = re.findall(r'<think>(.*?)</think>', content, re.DOTALL)
    think_text = '\n\n'.join(t.strip() for t in think_matches) if think_matches else ''
    content = _THINK_RE.sub('', content).strip()
    return think_text, content, input_tokens, output_tokens


def generate_insertion(context_before, context_after, instruction,
                       retrieved_docs=None):
    """
    Generate text to insert at a cursor position based on surrounding context.

    Args:
        context_before: Editor text before the cursor
        context_after: Editor text after the cursor
        instruction: The insertion instruction from the user
        retrieved_docs: Optional RAG context documents from vector store

    Returns:
        (think_text, inserted_text, input_tokens, output_tokens)
    """
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        raise ValueError("OPENTHAI_API_KEY not found")

    # ── RAG context: relevance-filtered ──────────────────────────────────────
    _MAX_DOC_CHARS = 1200
    _MAX_CONTEXT_CHARS = 4000
    context_text = ""
    rag_instruction = ""
    if retrieved_docs:
        parts = [doc.page_content[:_MAX_DOC_CHARS] for doc in retrieved_docs]
        raw_context = "\n\n".join(parts)[:_MAX_CONTEXT_CHARS]
        # Use instruction + surrounding text snippets for relevance check
        surrounding = (context_before[-150:] + " " + context_after[:150]).strip()
        relevance = _rag_relevance_score(f"{instruction} {surrounding}", raw_context)
        if relevance >= 0.15:
            context_text = raw_context
            rag_instruction = (
                "\n- ใช้ข้อมูลจากบริบทเอกสารที่ให้มาเพื่อความถูกต้องของเนื้อหาหากเกี่ยวข้อง"
                "\n- ห้ามแต่งข้อมูล ตัวเลข หรือการอ้างอิงที่ไม่ปรากฏในเอกสาร"
            )

    # ── No-RAG fallback signal ────────────────────────────────────────────────
    no_rag_notice_ins = (
        "" if context_text else
        "\nไม่มีเอกสารอ้างอิง — เขียนจากบริบทรอบข้างและความรู้ทั่วไปเท่านั้น "
        "ห้ามเพิ่มข้อมูลจำเพาะที่ไม่มีหลักฐาน"
    )

    system_prompt = (
        "คุณเป็นผู้ช่วยเขียนข้อความ ผู้ใช้จะระบุบริบทก่อนและหลังตำแหน่งที่ต้องการแทรก "
        "พร้อมคำสั่งว่าต้องการแทรกข้อความอะไร\n"
        "ตอบเฉพาะข้อความที่ต้องแทรกเท่านั้น ห้ามมีคำอธิบายหรือข้อความเพิ่มเติม "
        "ห้ามใส่เครื่องหมายคำพูดครอบข้อความ\n"
        "เขียนให้เข้ากับบริบทรอบข้าง รักษาภาษาเดิม หากคำสั่งเป็นภาษาไทยให้ตอบเป็นภาษาไทย\n"
        "ห้ามสร้างข้อมูล ชื่อ หรือตัวเลขที่ไม่มีหลักฐาน"
        + rag_instruction
        + no_rag_notice_ins + "\n\n"
        "ตรวจสอบภายในก่อนส่ง (ไม่ต้องแสดงในผลลัพธ์):\n"
        "1. ข้อความที่แทรกต่อเนื่องและสอดคล้องกับบริบทก่อน-หลัง\n"
        "2. ไม่มีข้อมูลจำเพาะ (ตัวเลข ชื่อ สถิติ) ที่ไม่ปรากฏในบริบทหรือในเอกสาร\n"
        "3. ตรงตามคำสั่งที่ได้รับ ไม่เกินขอบเขตที่กำหนด"
    )
    before_preview = context_before[-300:] if len(context_before) > 300 else context_before
    after_preview = context_after[:300] if len(context_after) > 300 else context_after

    user_parts = [
        f"บริบทก่อนตำแหน่งแทรก:\n\"\"\"\n{before_preview}\n\"\"\"",
        f"บริบทหลังตำแหน่งแทรก:\n\"\"\"\n{after_preview}\n\"\"\"",
        f"คำสั่ง: {instruction}",
    ]
    if context_text:
        user_parts.append(f"=== บริบทจากเอกสาร (ใช้อ้างอิงหากเกี่ยวข้อง) ===\n{context_text}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    content, input_tokens, output_tokens = _call_api(
        messages, api_key, max_tokens=2048, temperature=0.3
    )
    think_matches = re.findall(r'<think>(.*?)</think>', content, re.DOTALL)
    think_text = '\n\n'.join(t.strip() for t in think_matches) if think_matches else ''
    content = _THINK_RE.sub('', content).strip()
    return think_text, content, input_tokens, output_tokens


def generate_answer_stream(query: str, retrieved_docs: list,
                           chat_history: Optional[list] = None,
                           editor_content: Optional[str] = None) -> Generator[str, None, None]:
    """
    Streaming variant of generate_answer for plain chat mode (non-research).

    Yields tokens as they arrive from the OpenThaiGPT API so that Streamlit's
    st.write_stream() can render them incrementally, dramatically improving
    perceived latency.

    Only supports plain chat mode — research mode requires structured JSON
    output and cannot be streamed reliably.

    Args:
        query: User message
        retrieved_docs: RAG context documents (may be empty)
        chat_history: Previous messages for context
        editor_content: Current editor text (unused in chat mode but kept for API symmetry)

    Yields:
        str: Token chunks

    Raises:
        ValueError: On API key error or HTTP 4xx errors
    """
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENTHAI_API_KEY not found. "
            f"Please set OPENTHAI_API_KEY in {env_path.resolve()}"
        )

    has_context = bool(retrieved_docs)
    _MAX_DOC_CHARS = 1500
    _MAX_CONTEXT_CHARS = 6000
    if retrieved_docs:
        parts = [doc.page_content[:_MAX_DOC_CHARS] for doc in retrieved_docs]
        context_text = "\n\n".join(parts)[:_MAX_CONTEXT_CHARS]
    else:
        context_text = ""

    language_instruction = (
        "ภาษา: ตอบภาษาเดียวกับคำถาม (default ไทย), technical terms ใช้อังกฤษได้"
    )

    if has_context:
        knowledge_instruction = (
            "ใช้บริบทจากเอกสารด้านล่างเป็นหลักในการตอบ "
            "หากบริบทไม่เพียงพอ ให้ขึ้นต้นคำตอบด้วย "
            "\"⚠️ หมายเหตุ: ข้อมูลนี้มาจากความรู้ทั่วไปของ AI\""
        )
    else:
        knowledge_instruction = (
            "ไม่มีเอกสารถูกโหลดไว้ — ตอบจากความรู้ทั่วไปของ AI ได้เลย"
        )

    editor_awareness = ""
    if editor_content and editor_content.strip():
        editor_awareness = (
            "\nคุณรับรู้เนื้อหาในตัวแก้ไขของผู้ใช้ สามารถตอบคำถามเกี่ยวกับเนื้อหานั้นได้"
        )

    system_prompt = (
        "คุณเป็นผู้ช่วยวิจัย ทำหน้าที่ตอบคำถามอย่างตรงประเด็นและถูกต้อง\n"
        f"{knowledge_instruction}\n"
        f"{language_instruction}"
        f"{editor_awareness}\n\n"
        "แนวทางการตอบ:\n"
        "- ตอบตรงคำถาม ชัดเจน ครบถ้วน\n"
        "- ตอบเป็น plain text ธรรมดา ไม่ต้องมี JSON"
    )

    user_parts = [f"คำถาม: {query}"]
    if context_text:
        user_parts.append(f"=== บริบทจากเอกสาร ===\n{context_text}")
    # Include editor content (truncated to 800 chars) for context awareness
    if editor_content and editor_content.strip():
        editor_tail = editor_content.strip()[-800:]
        user_parts.append(f"=== เนื้อหาในตัวแก้ไขปัจจุบัน (ส่วนท้าย) ===\n{editor_tail}")

    messages = [{"role": "system", "content": system_prompt}]
    if chat_history:
        for msg in chat_history[-4:]:
            role = msg.get("role", "user")
            content = _THINK_RE.sub('', msg.get("content", "")).strip()[:400]
            if content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": "\n\n".join(user_parts)})

    try:
        yield from _call_api_stream(messages, api_key, max_tokens=2048, temperature=0.3)
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        if status in (401, 403):
            raise ValueError(f"API key invalid or unauthorized (HTTP {status}).")
        elif status == 429:
            raise ValueError("API quota exceeded (429). รอสักครู่แล้วลองใหม่")
        else:
            raise ValueError(f"HTTP error {status}: {e}")
    except requests.RequestException as e:
        raise ValueError(f"Request failed: {e}")


def generate_research_guide(
    topic: str,
    retrieved_docs: list = None,
    existing_content: str = "",
) -> tuple[str, int, int]:
    """
    Non-streaming research guide generation.
    Uses RAG context from KB + LLM general knowledge.
    Returns (content, input_tokens, output_tokens).
    """
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        raise ValueError("OPENTHAI_API_KEY not found")

    # Build context from retrieved docs
    _MAX_DOC_CHARS = 2000
    _MAX_CONTEXT_CHARS = 6000
    context_text = ""
    if retrieved_docs:
        parts = []
        for doc in retrieved_docs:
            title = doc.metadata.get("paper_title", doc.metadata.get("doc_name", ""))
            snippet = doc.page_content[:_MAX_DOC_CHARS]
            parts.append(f"[{title}]\n{snippet}")
        context_text = "\n\n".join(parts)[:_MAX_CONTEXT_CHARS]

    existing_tail = existing_content.strip()[-1000:] if existing_content and existing_content.strip() else ""

    system_prompt = (
        "คุณเป็นที่ปรึกษาวิจัยผู้เชี่ยวชาญ มีประสบการณ์ด้านการวิจัยและให้คำปรึกษาวิทยานิพนธ์มากกว่า 20 ปี\n"
        "ภารกิจ: สร้างแนวทางการวิจัย (Research Guide) สำหรับหัวข้อที่กำหนด\n\n"
        "โครงสร้างที่ต้องการ (ใช้ Markdown):\n\n"
        "## 1. การกำหนดคำถามวิจัย (Research Questions)\n"
        "- แนะนำคำถามวิจัยที่เป็นไปได้ 2-3 ข้อ พร้อมอธิบายเหตุผลว่าทำไมคำถามเหล่านี้สำคัญ\n"
        "- ระบุตัวแปรต้น ตัวแปรตาม (ถ้ามี)\n\n"
        "## 2. การทบทวนวรรณกรรมที่แนะนำ (Suggested Literature Review)\n"
        "- ระบุขอบเขตทฤษฎีและแนวคิดที่ควรศึกษา\n"
        "- หากมีเอกสารจากแหล่งความรู้ ให้อ้างอิงเอกสารที่เกี่ยวข้อง\n"
        "- แนะนำคำค้นหา (Keywords) สำหรับการสืบค้นงานวิจัยเพิ่มเติม\n\n"
        "## 3. ระเบียบวิธีวิจัยที่เหมาะสม (Research Methodology)\n"
        "- แนะนำแนวทางวิจัย (เชิงปริมาณ/เชิงคุณภาพ/ผสมผสาน) พร้อมเหตุผล\n"
        "- กลุ่มตัวอย่างและวิธีสุ่มที่เหมาะสม\n"
        "- เครื่องมือวิจัยที่แนะนำ\n"
        "- วิธีการวิเคราะห์ข้อมูล\n\n"
        "## 4. แผนการดำเนินงาน (Research Plan)\n"
        "- ขั้นตอนการวิจัยเป็นลำดับ\n"
        "- ระยะเวลาโดยประมาณ\n"
        "- ทรัพยากรที่ต้องการ\n\n"
        "## 5. ข้อควรระวังและข้อจำกัด (Limitations & Considerations)\n"
        "- จุดที่ต้องระวังในการทำวิจัยเรื่องนี้\n"
        "- จริยธรรมการวิจัย (ถ้าเกี่ยวข้อง)\n"
        "- ข้อจำกัดที่อาจเกิดขึ้น\n\n"
        "กฎ:\n"
        "- ตอบเป็นภาษาไทย, technical terms ใช้อังกฤษในวงเล็บ\n"
        "- หากมีบริบทจากเอกสาร ให้อ้างอิงข้อมูลจากเอกสารเป็นหลัก\n"
        "- หากไม่มีบริบทจากเอกสาร ให้ใช้ความรู้ทั่วไปและระบุว่าเป็นคำแนะนำจาก AI\n"
        "- ความยาวขั้นต่ำ 600 คำ\n"
        "- ตอบเป็น plain text / Markdown เท่านั้น ห้าม JSON"
    )

    user_parts = [f"หัวข้อวิจัย: {topic}"]
    if context_text:
        user_parts.append(f"=== เอกสารจากแหล่งความรู้ ===\n{context_text}")
    if existing_tail:
        user_parts.append(f"=== เนื้อหาที่เขียนไปแล้ว (ส่วนท้าย) ===\n{existing_tail}")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]
    return _call_api(messages, api_key, max_tokens=6144, temperature=0.4)


def print_generated_answer(query, answer):
    """
    Pretty-print the AI-generated answer (CLI use).
    """
    print("\n" + "╔" + "═" * 78 + "╗")
    print("║" + "🤖 AI-GENERATED ANSWER".center(78) + "║")
    print("╠" + "═" * 78 + "╣")
    print(f"║ Question: {query}")
    print("║" + "─" * 78 + "║")
    print("║ Answer:")
    print("║" + "─" * 78 + "║")
    for line in answer.split('\n'):
        if line.strip():
            wrapped_lines = textwrap.wrap(line, width=70)
            for wrapped_line in wrapped_lines:
                print(f"║ {wrapped_line:<76} ║")
        else:
            print("║" + " " * 78 + "║")
    print("╚" + "═" * 78 + "╝\n")
