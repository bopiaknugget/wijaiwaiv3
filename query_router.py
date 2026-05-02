"""
Query Router Module — Hybrid Classification for RAG Pipeline

Classifies user queries into categories to enable smarter retrieval:
- Fast rule-based classification (regex + keyword matching)
- LLM fallback for ambiguous queries via OpenThaiGPT API
- Supports Thai and English queries
- Returns a QueryClassification with category, confidence, and suggested topK

Categories:
    research  — academic/scientific queries
    code      — programming/technical queries
    general   — broad knowledge queries
    unknown   — could not classify (triggers LLM fallback if enabled)
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv


# ── Classification result ────────────────────────────────────────────────────

@dataclass
class QueryClassification:
    """Result of query classification."""
    category: str           # "research", "code", "general", "unknown"
    confidence: float       # 0.0–1.0 (rule-based: 0.8+, LLM: 0.6)
    suggested_top_k: int    # recommended topK for this query type
    source: str             # "rule" or "llm"


# ── TopK mapping by category ────────────────────────────────────────────────

_TOPK_MAP = {
    "research": 5,
    "code": 5,
    "general": 10,
    "unknown": 15,
}


# ── Rule-based patterns (Thai + English) ────────────────────────────────────

_RESEARCH_PATTERNS = re.compile(
    r"("
    # English research terms
    r"research|study|paper|thesis|dissertation|journal|"
    r"literature\s+review|methodology|hypothesis|"
    r"experiment|finding|result|conclusion|abstract|"
    r"citation|reference|peer[\s-]?review|"
    r"analysis|framework|model|theory|"
    r"quantitative|qualitative|empirical|"
    r"survey|interview|sample\s+size|"
    r"statistical|regression|correlation|p[\s-]?value|"
    r"systematic\s+review|meta[\s-]?analysis|"
    # Thai research terms
    r"วิจัย|การศึกษา|บทความ|วิทยานิพนธ์|"
    r"ทบทวนวรรณกรรม|ระเบียบวิธี|สมมติฐาน|"
    r"การทดลอง|ผลการ|สรุป|บทคัดย่อ|"
    r"อ้างอิง|เอกสารอ้างอิง|บรรณานุกรม|"
    r"วิเคราะห์|กรอบแนวคิด|ทฤษฎี|"
    r"เชิงปริมาณ|เชิงคุณภาพ|เชิงประจักษ์|"
    r"แบบสอบถาม|กลุ่มตัวอย่าง|"
    r"สถิติ|การถดถอย|สหสัมพันธ์"
    r")",
    re.IGNORECASE,
)

_CODE_PATTERNS = re.compile(
    r"("
    # Programming language names and tools
    r"\bpython\b|\bjava\b|\bjavascript\b|\btypescript\b|\bc\+\+\b|"
    r"\brust\b|\bgo\b|\bsql\b|\bhtml\b|\bcss\b|"
    r"\breact\b|\bvue\b|\bangular\b|\bdjango\b|\bflask\b|\bfastapi\b|"
    r"\bstreamlit\b|\bpandas\b|\bnumpy\b|\btensorflow\b|\bpytorch\b|"
    # Code concepts
    r"\bfunction\b|\bclass\b|\bmethod\b|\bapi\b|\bendpoint\b|"
    r"\bvariable\b|\bloop\b|\barray\b|\blist\b|\bdict\b|"
    r"\bdebug\b|\berror\b|\bbug\b|\bexception\b|\btraceback\b|"
    r"\bimport\b|\bpackage\b|\blibrary\b|\bmodule\b|\bpip\b|\bnpm\b|"
    r"\bgit\b|\bdocker\b|\bkubernetes\b|\bci/cd\b|"
    r"\balgorithm\b|\bdata\s+structure\b|\bcomplexity\b|"
    r"\bsyntax\b|\bcompile\b|\bruntime\b|"
    # Code formatting clues
    r"```|def\s+\w+|import\s+\w+|\bprint\(|"
    # Thai code terms
    r"โค้ด|เขียนโปรแกรม|ฟังก์ชัน|คลาส|"
    r"แก้บัค|ข้อผิดพลาด|"
    r"อัลกอริทึม|โครงสร้างข้อมูล"
    r")",
    re.IGNORECASE,
)

_GENERAL_PATTERNS = re.compile(
    r"("
    # General knowledge indicators
    r"what\s+is|who\s+is|when\s+did|where\s+is|how\s+does|why\s+does|"
    r"explain|describe|tell\s+me|define|meaning\s+of|"
    r"คืออะไร|หมายความว่า|อธิบาย|บอก|เล่า|แนะนำ|"
    r"ประวัติ|ที่มา|ความหมาย"
    r")",
    re.IGNORECASE,
)


def _rule_classify(query: str) -> QueryClassification:
    """
    Classify a query using regex pattern matching.

    Checks research patterns first (highest priority for academic workbench),
    then code, then general. Returns 'unknown' if no pattern matches.

    Args:
        query: User query string

    Returns:
        QueryClassification with category and confidence
    """
    query_stripped = query.strip()

    # Count matches for each category to determine strongest signal
    research_matches = len(_RESEARCH_PATTERNS.findall(query_stripped))
    code_matches = len(_CODE_PATTERNS.findall(query_stripped))
    general_matches = len(_GENERAL_PATTERNS.findall(query_stripped))

    # Pick the category with the most matches
    scores = {
        "research": research_matches,
        "code": code_matches,
        "general": general_matches,
    }
    best_category = max(scores, key=scores.get)
    best_score = scores[best_category]

    if best_score == 0:
        return QueryClassification(
            category="unknown",
            confidence=0.3,
            suggested_top_k=_TOPK_MAP["unknown"],
            source="rule",
        )

    # Confidence scales with number of matches (capped at 0.95)
    confidence = min(0.6 + (best_score * 0.1), 0.95)

    return QueryClassification(
        category=best_category,
        confidence=confidence,
        suggested_top_k=_TOPK_MAP[best_category],
        source="rule",
    )


def _llm_classify(query: str, api_key: str) -> QueryClassification:
    """
    Classify a query using OpenThaiGPT LLM as fallback.

    Only called when rule-based classification returns 'unknown'.
    Uses a minimal prompt to keep latency low.

    Args:
        query: User query string
        api_key: OpenThaiGPT API key

    Returns:
        QueryClassification from LLM response
    """
    system_prompt = (
        "Classify the user query into exactly one category.\n"
        "Categories: research, code, general\n"
        "Reply with ONLY the category name, nothing else."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query[:500]},  # Truncate long queries
    ]

    try:
        response = requests.post(
            "http://thaillm.or.th/api/openthaigpt/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "apikey": api_key,
            },
            json={
                "model": "/model",
                "messages": messages,
                "max_tokens": 20,
                "temperature": 0.0,
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        raw = data["choices"][0]["message"]["content"].strip().lower()

        # Parse the LLM response — look for known categories
        for cat in ("research", "code", "general"):
            if cat in raw:
                return QueryClassification(
                    category=cat,
                    confidence=0.6,
                    suggested_top_k=_TOPK_MAP[cat],
                    source="llm",
                )

        # LLM returned something unexpected
        return QueryClassification(
            category="general",
            confidence=0.4,
            suggested_top_k=_TOPK_MAP["general"],
            source="llm",
        )

    except Exception as e:
        print(f"Warning: LLM classification failed: {e}")
        # On failure, default to general with low confidence
        return QueryClassification(
            category="general",
            confidence=0.3,
            suggested_top_k=_TOPK_MAP["general"],
            source="rule",
        )


def classify_query(query: str, use_llm_fallback: bool = False) -> QueryClassification:
    """
    Classify a query using hybrid approach: rules first, LLM fallback if needed.

    This is the main entry point for query classification. It is designed
    to be fast (rule-based) for the common case and only invokes the LLM
    when the query is genuinely ambiguous.

    Args:
        query: User query string (Thai or English)
        use_llm_fallback: If True, call LLM when rule-based returns 'unknown'.
                          Default False to avoid extra API latency.

    Returns:
        QueryClassification with category, confidence, suggested_top_k, source
    """
    if not query or not query.strip():
        return QueryClassification(
            category="unknown",
            confidence=0.0,
            suggested_top_k=_TOPK_MAP["unknown"],
            source="rule",
        )

    # Step 1: Fast rule-based classification
    result = _rule_classify(query)

    # Step 2: LLM fallback if unknown and enabled
    if result.category == "unknown" and use_llm_fallback:
        env_path = Path(__file__).parent / ".env"
        load_dotenv(dotenv_path=env_path)
        api_key = os.getenv("OPENTHAI_API_KEY", "")
        if api_key:
            result = _llm_classify(query, api_key)

    return result
