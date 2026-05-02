"""
Web Scraping Module — ดึงข้อมูลจากเว็บไซต์, สรุปเนื้อหา, และตั้งชื่อ Title อัตโนมัติ
ใช้ trafilatura สำหรับดึงเนื้อหาหลัก พร้อม BeautifulSoup เป็น fallback
เชื่อมต่อ OpenThaiGPT API สำหรับการสรุปและตั้งชื่อ
"""

import os
import re
import requests
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# โหลด API key จาก .env
_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

API_URL = "http://thaillm.or.th/api/openthaigpt/v1/chat/completions"
MODEL = "/model"

# ── User-Agent สำหรับ HTTP request เพื่อลดโอกาสถูก block ──
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "th,en;q=0.9",
}


def _call_api(messages: list, api_key: str,
              max_tokens: int = 2048, temperature: float = 0.3) -> tuple:
    """
    เรียก OpenThaiGPT API
    Returns: (content, input_tokens, output_tokens)
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


# ============================================================================
# Step 1: Web Scraping — ดึงเนื้อหาจาก URL
# ============================================================================

def scrape_url(url: str, timeout: int = 30) -> dict:
    """
    ดึงเนื้อหาข้อความจาก URL ที่ระบุ

    ใช้ trafilatura เป็นตัวหลัก (ดึงเนื้อหาบทความได้สะอาด)
    ใช้ BeautifulSoup เป็น fallback กรณี trafilatura ดึงไม่ได้

    Args:
        url: URL ของหน้าเว็บที่ต้องการดึงข้อมูล
        timeout: ระยะเวลา timeout สำหรับ HTTP request (วินาที)

    Returns:
        dict: {
            'success': bool,
            'content': str,     # เนื้อหาที่ดึงได้ (ถ้าสำเร็จ)
            'error': str,       # ข้อความ error (ถ้าล้มเหลว)
            'url': str,         # URL ที่ใช้
        }
    """
    # ตรวจสอบ URL เบื้องต้น
    if not url or not url.strip():
        return {'success': False, 'content': '', 'error': 'กรุณาระบุ URL', 'url': url}

    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    # ── ดึง HTML จาก URL ──
    try:
        response = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    except requests.exceptions.Timeout:
        return {
            'success': False, 'content': '', 'url': url,
            'error': f'หมดเวลาการเชื่อมต่อ (Timeout {timeout} วินาที) — เว็บไซต์อาจตอบสนองช้าหรือไม่พร้อมใช้งาน'
        }
    except requests.exceptions.ConnectionError:
        return {
            'success': False, 'content': '', 'url': url,
            'error': 'ไม่สามารถเชื่อมต่อได้ — กรุณาตรวจสอบ URL หรือการเชื่อมต่ออินเทอร์เน็ต'
        }
    except requests.exceptions.TooManyRedirects:
        return {
            'success': False, 'content': '', 'url': url,
            'error': 'เว็บไซต์มีการ redirect มากเกินไป — กรุณาตรวจสอบ URL'
        }
    except requests.exceptions.RequestException as e:
        return {
            'success': False, 'content': '', 'url': url,
            'error': f'เกิดข้อผิดพลาดในการเชื่อมต่อ: {str(e)}'
        }

    # ── ตรวจสอบ HTTP status code ──
    if response.status_code == 403:
        return {
            'success': False, 'content': '', 'url': url,
            'error': 'เว็บไซต์ปฏิเสธการเข้าถึง (403 Forbidden) — เว็บไซต์อาจมีการป้องกัน bot'
        }
    elif response.status_code == 404:
        return {
            'success': False, 'content': '', 'url': url,
            'error': 'ไม่พบหน้าเว็บที่ระบุ (404 Not Found) — กรุณาตรวจสอบ URL'
        }
    elif response.status_code >= 400:
        return {
            'success': False, 'content': '', 'url': url,
            'error': f'เว็บไซต์ตอบกลับด้วย HTTP Error {response.status_code}'
        }

    html = response.text

    # ── ลองใช้ trafilatura ก่อน (ดึงเนื้อหาบทความได้สะอาดกว่า) ──
    content = ''
    try:
        import trafilatura
        content = trafilatura.extract(html, include_comments=False,
                                      include_tables=True, favor_recall=True) or ''
    except ImportError:
        pass  # ถ้าไม่มี trafilatura ก็ใช้ BeautifulSoup แทน
    except Exception:
        pass  # trafilatura อาจล้มเหลวกับบาง HTML

    # ── Fallback: ใช้ BeautifulSoup ──
    if not content.strip():
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            # ลบ script, style, nav, footer, header ที่ไม่ใช่เนื้อหา
            for tag in soup.find_all(['script', 'style', 'nav', 'footer',
                                       'header', 'aside', 'iframe', 'noscript']):
                tag.decompose()

            # ลองดึงจาก <article> หรือ <main> ก่อน
            main_content = soup.find('article') or soup.find('main')
            if main_content:
                content = main_content.get_text(separator='\n', strip=True)
            else:
                # ดึงจาก body ทั้งหมด
                body = soup.find('body')
                if body:
                    content = body.get_text(separator='\n', strip=True)
                else:
                    content = soup.get_text(separator='\n', strip=True)
        except ImportError:
            return {
                'success': False, 'content': '', 'url': url,
                'error': 'ไม่พบไลบรารี trafilatura หรือ beautifulsoup4 — กรุณาติดตั้ง: pip install trafilatura beautifulsoup4'
            }
        except Exception as e:
            return {
                'success': False, 'content': '', 'url': url,
                'error': f'เกิดข้อผิดพลาดในการแยกเนื้อหา: {str(e)}'
            }

    # ── ทำความสะอาดเนื้อหา ──
    content = _clean_text(content)

    # ── ตรวจสอบว่าเนื้อหาว่างเปล่าหรือไม่ ──
    if not content or len(content.strip()) < 50:
        return {
            'success': False, 'content': '', 'url': url,
            'error': 'ไม่สามารถดึงเนื้อหาจากหน้าเว็บได้ — เว็บอาจใช้ JavaScript ในการโหลดเนื้อหา หรือเนื้อหาสั้นเกินไป'
        }

    return {'success': True, 'content': content, 'error': '', 'url': url}


def _clean_text(text: str) -> str:
    """ทำความสะอาดข้อความ — ลบช่องว่างซ้ำ, บรรทัดว่างซ้ำ"""
    if not text:
        return ''
    # ลบ whitespace ซ้ำในแต่ละบรรทัด
    lines = [line.strip() for line in text.split('\n')]
    # ลบบรรทัดว่างซ้ำ (เก็บไว้แค่ 1 บรรทัดว่าง)
    cleaned = []
    prev_empty = False
    for line in lines:
        if not line:
            if not prev_empty:
                cleaned.append('')
            prev_empty = True
        else:
            cleaned.append(line)
            prev_empty = False
    return '\n'.join(cleaned).strip()


# ============================================================================
# Step 1: AI Functions — สรุปเนื้อหาและตั้งชื่อ Title
# ============================================================================

def summarize_content(content: str) -> dict:
    """
    สรุปเนื้อหาที่ดึงมาจากเว็บ โดยใช้ OpenThaiGPT API
    คงรายละเอียดและข้อมูลสำคัญไว้อย่างครบถ้วน

    Args:
        content: เนื้อหาที่ต้องการสรุป

    Returns:
        dict: {
            'success': bool,
            'summary': str,
            'input_tokens': int,
            'output_tokens': int,
            'error': str
        }
    """
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        return {
            'success': False, 'summary': '', 'error': 'ไม่พบ OPENTHAI_API_KEY ใน .env',
            'input_tokens': 0, 'output_tokens': 0
        }

    # ตัดเนื้อหาที่ยาวเกินไป (ป้องกัน token limit)
    max_chars = 8000
    truncated = content[:max_chars] if len(content) > max_chars else content

    system_prompt = (
        "สรุปข้อมูลจากเนื้อหาที่กำหนดให้ โดยต้องคงรายละเอียดและข้อมูลสำคัญไว้อย่างครบถ้วน "
        "ไม่ตัดทอนสาระสำคัญ"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"กรุณาสรุปเนื้อหาต่อไปนี้:\n\n{truncated}"},
    ]

    try:
        summary, input_tokens, output_tokens = _call_api(
            messages, api_key, max_tokens=3000, temperature=0.3
        )
        # ลบ <think> tags ออก
        summary = re.sub(r'<think>.*?</think>', '', summary, flags=re.DOTALL).strip()
        return {
            'success': True, 'summary': summary, 'error': '',
            'input_tokens': input_tokens, 'output_tokens': output_tokens
        }
    except Exception as e:
        return {
            'success': False, 'summary': '', 'error': f'เกิดข้อผิดพลาดในการสรุป: {str(e)}',
            'input_tokens': 0, 'output_tokens': 0
        }


def generate_title(content: str) -> dict:
    """
    ตั้งชื่อ Title อัตโนมัติจากเนื้อหา โดยใช้ OpenThaiGPT API

    Args:
        content: เนื้อหาที่ต้องการตั้งชื่อ

    Returns:
        dict: {
            'success': bool,
            'title': str,
            'input_tokens': int,
            'output_tokens': int,
            'error': str
        }
    """
    api_key = os.getenv("OPENTHAI_API_KEY")
    if not api_key:
        return {
            'success': False, 'title': '', 'error': 'ไม่พบ OPENTHAI_API_KEY ใน .env',
            'input_tokens': 0, 'output_tokens': 0
        }

    # ใช้เนื้อหา 2000 ตัวอักษรแรกสำหรับตั้งชื่อ
    preview = content[:2000]

    system_prompt = (
        "คุณเป็นผู้ช่วยตั้งชื่อเรื่อง ให้ตั้งชื่อสั้นกระชับ สื่อความหมาย "
        "ตอบเฉพาะชื่อเรื่องเท่านั้น ไม่ต้องมีคำอธิบายเพิ่มเติม "
        "ไม่ต้องใส่เครื่องหมายคำพูด ความยาวไม่เกิน 100 ตัวอักษร"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"ตั้งชื่อเรื่องให้เนื้อหานี้:\n\n{preview}"},
    ]

    try:
        title, input_tokens, output_tokens = _call_api(
            messages, api_key, max_tokens=128, temperature=0.3
        )
        # ลบ <think> tags และทำความสะอาด
        title = re.sub(r'<think>.*?</think>', '', title, flags=re.DOTALL).strip()
        title = title.strip('"\'')[:100]  # จำกัดความยาว
        return {
            'success': True, 'title': title, 'error': '',
            'input_tokens': input_tokens, 'output_tokens': output_tokens
        }
    except Exception as e:
        return {
            'success': False, 'title': '', 'error': f'เกิดข้อผิดพลาดในการตั้งชื่อ: {str(e)}',
            'input_tokens': 0, 'output_tokens': 0
        }


# ============================================================================
# Step 2: Chunking & Database Integration
# ============================================================================

def prepare_web_chunks(summary: str, title: str, url: str,
                       source_type: str = "web_page",
                       web_page_id: int = 0) -> tuple:
    """
    เตรียม chunks จากเนื้อหาที่สรุปแล้ว สำหรับบันทึกลง vector store
    ใช้ระบบ Parent-Child Chunking เหมือนกับเอกสารทั่วไป

    Args:
        summary: เนื้อหาที่ผ่านการสรุปแล้ว
        title: ชื่อเรื่อง
        url: URL ต้นทาง
        source_type: ประเภทแหล่งข้อมูล (default: "web_page")
        web_page_id: ID ของ record ใน web_pages table (ใช้ reference สำหรับแก้ไข)

    Returns:
        tuple: (child_documents, parent_records)
    """
    import uuid
    from datetime import date
    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from document_loader import get_adaptive_chunk_params

    total_chars = len(summary)
    chunk_size, chunk_overlap = get_adaptive_chunk_params(total_chars)

    # สร้าง parent document (เนื้อหาเต็ม)
    parent_id = f"parent_{uuid.uuid4().hex[:12]}"
    parent_records = [{
        'id': parent_id,
        'content': summary,
        'source_file': url,
        'page_number': 0,
        'section': 'web_content',
    }]

    # สร้าง Document object พร้อม metadata ที่บอกว่ามาจาก web
    doc = Document(
        page_content=summary,
        metadata={
            'source_type': source_type,
            'doc_name': url,
            'paper_title': title,
            'authors': '',
            'year': '',
            'section': 'web_content',
            'created_at': date.today().isoformat(),
            'url': url,
            'web_page_id': web_page_id,
            'chunk_type': 'child',
            'parent_id': parent_id,
        }
    )

    # ทำ Chunking ด้วย adaptive sizing
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", " ", ""]
    )
    child_chunks = splitter.split_documents([doc])

    # ให้แต่ละ chunk มี parent_id
    for chunk in child_chunks:
        chunk.metadata['parent_id'] = parent_id
        chunk.metadata['chunk_type'] = 'child'

    print(f"✓ Web chunking: {len(parent_records)} parent, "
          f"{len(child_chunks)} children (chunk_size={chunk_size})")

    return child_chunks, parent_records
