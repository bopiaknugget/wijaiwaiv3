"""
test_token_caps.py — Verify token-cap fixes in generator.py and reviewer.py.

Rules:
- No real HTTP calls: requests.post is mocked throughout.
- All assertions target the JSON payload sent to the mock, not the return value.
- Uses 3 chars/token as conservative Thai-text estimate.
"""

import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

# ---------------------------------------------------------------------------
# Ensure project root is importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Patch OPENTHAI_API_KEY in the environment so modules don't raise on import
os.environ.setdefault("OPENTHAI_API_KEY", "test-mock-key")

# ---------------------------------------------------------------------------
# Build a realistic mock response for requests.post
# ---------------------------------------------------------------------------
FAKE_CONTENT = "นี่คือคำตอบจำลองสำหรับการทดสอบ (mock response for testing)"

def _make_mock_response(content: str = FAKE_CONTENT) -> MagicMock:
    """Return a mock requests.Response that looks like a real API reply."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()  # no-op
    mock_resp.json.return_value = {
        "choices": [
            {"message": {"content": content}}
        ],
        "usage": {
            "prompt_tokens": 50,
            "completion_tokens": 50,
        },
    }
    # headers needed by _call_api_stream (not used here but safe to set)
    mock_resp.headers = {"Content-Type": "application/json"}
    return mock_resp


# ---------------------------------------------------------------------------
# Helper: extract the full user-message text from a captured call
# ---------------------------------------------------------------------------
def _get_user_message(call_kwargs: dict) -> str:
    """Pull the last user-role message content from the captured payload."""
    payload = call_kwargs.get("json")
    if payload is None and "data" in call_kwargs:
        payload = json.loads(call_kwargs["data"])
    messages = payload["messages"]
    # The last message is always the user turn in these functions
    for msg in reversed(messages):
        if msg["role"] == "user":
            return msg["content"]
    return ""


def _full_payload_chars(call_kwargs: dict) -> int:
    """Total chars across all messages in the payload (system + user + history)."""
    payload = call_kwargs.get("json")
    if payload is None and "data" in call_kwargs:
        payload = json.loads(call_kwargs["data"])
    messages = payload["messages"]
    return sum(len(m.get("content", "")) for m in messages)


# ---------------------------------------------------------------------------
# Minimal Doc stub so we don't need LangChain / Pinecone installed
# ---------------------------------------------------------------------------
class FakeDoc:
    """Minimal stand-in for a LangChain Document."""
    def __init__(self, content: str):
        self.page_content = content
        self.metadata = {}


# ---------------------------------------------------------------------------
# PASS / FAIL printer
# ---------------------------------------------------------------------------
_results = []

def _report(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    msg = f"[{status}] {name}"
    if detail:
        msg += f"\n       {detail}"
    print(msg)
    _results.append((name, passed))


# ===========================================================================
# TEST 1 — generate_section: docs overload (5 × 3000 chars = 15 000 raw)
# ===========================================================================
def test_generate_section_doc_cap():
    """Context sent to API must be <= 6000 chars total even with 15k raw input."""
    from generator import generate_section

    big_docs = [FakeDoc("A" * 3000) for _ in range(5)]  # 5 × 3000 = 15 000 chars

    mock_resp = _make_mock_response()
    with patch("generator.requests.post", return_value=mock_resp) as mock_post:
        generate_section(
            topic="Test Topic",
            section_instruction="Write section 1",
            retrieved_docs=big_docs,
            existing_content="",
        )
        call_kwargs = mock_post.call_args[1]  # kwargs of the call

    user_msg = _get_user_message(call_kwargs)
    # The context block appears after "=== บริบทจากเอกสาร ==="
    context_marker = "=== บริบทจากเอกสาร ==="
    if context_marker in user_msg:
        # Everything after the marker (and the newline) is the context block
        idx = user_msg.index(context_marker) + len(context_marker)
        # The next block ends at the next "===" or end-of-string
        remainder = user_msg[idx:]
        next_block = remainder.find("===")
        context_block = remainder[:next_block].strip() if next_block != -1 else remainder.strip()
        actual_context_chars = len(context_block)
    else:
        actual_context_chars = 0

    # Also verify the total payload stays within a sane window
    total_chars = _full_payload_chars(call_kwargs)
    est_tokens = total_chars // 3
    window_ok = est_tokens <= (16384 - 4096)  # leave 4096 for output

    passed = actual_context_chars <= 6000
    _report(
        "generate_section: context chars <= 6000",
        passed,
        f"context_chars={actual_context_chars} (limit=6000) | "
        f"total_payload_chars={total_chars} | est_tokens={est_tokens} | "
        f"fits_window={'YES' if window_ok else 'NO'}",
    )
    return actual_context_chars


# ===========================================================================
# TEST 2 — generate_section: each individual doc is capped at 1500 chars
# ===========================================================================
def test_generate_section_per_doc_cap():
    """No single doc's content in the payload should exceed 1500 chars."""
    from generator import generate_section

    big_docs = [FakeDoc("B" * 3000) for _ in range(3)]

    mock_resp = _make_mock_response()
    with patch("generator.requests.post", return_value=mock_resp) as mock_post:
        generate_section(
            topic="Test Topic",
            section_instruction="Write section 2",
            retrieved_docs=big_docs,
            existing_content="",
        )
        call_kwargs = mock_post.call_args[1]

    user_msg = _get_user_message(call_kwargs)
    # Each doc was "B" * 3000; after cap it should be "B" * 1500.
    # The joined string would be "B"*1500 + "\n\n" + "B"*1500 + ...
    # Check no run of a single char exceeds 1500 consecutive
    import re as _re
    # Find the longest run of repeated 'B' chars
    runs = _re.findall(r'B+', user_msg)
    longest_run = max((len(r) for r in runs), default=0)
    passed = longest_run <= 1500
    _report(
        "generate_section: per-doc chars <= 1500",
        passed,
        f"longest_single_doc_run={longest_run} (limit=1500)",
    )


# ===========================================================================
# TEST 3 — generate_selection_edit: selected_text overload (10 000 chars)
# ===========================================================================
def test_generate_selection_edit_cap():
    """selected_text in payload must be <= 4000 chars."""
    from generator import generate_selection_edit

    long_text = "C" * 10000

    mock_resp = _make_mock_response()
    with patch("generator.requests.post", return_value=mock_resp) as mock_post:
        generate_selection_edit(
            selected_text=long_text,
            instruction="ปรับปรุงภาษาให้เป็นวิชาการ",
        )
        call_kwargs = mock_post.call_args[1]

    user_msg = _get_user_message(call_kwargs)
    # selected_text was "C"*10000, after cap "C"*4000
    import re as _re
    runs = _re.findall(r'C+', user_msg)
    longest_run = max((len(r) for r in runs), default=0)

    total_chars = _full_payload_chars(call_kwargs)
    est_tokens = total_chars // 3
    window_ok = est_tokens <= (16384 - 2048)

    passed = longest_run <= 4000
    _report(
        "generate_selection_edit: selected_text chars <= 4000",
        passed,
        f"selected_text_chars_in_payload={longest_run} (limit=4000) | "
        f"total_payload_chars={total_chars} | est_tokens={est_tokens} | "
        f"fits_window={'YES' if window_ok else 'NO'}",
    )
    return longest_run


# ===========================================================================
# TEST 4 — review_research: content overload (20 000 chars)
# ===========================================================================
def test_review_research_cap():
    """Content sent to API must be <= 8000 chars."""
    from reviewer import review_research

    long_content = "D" * 20000

    mock_resp = _make_mock_response()
    with patch("reviewer.requests.post", return_value=mock_resp) as mock_post:
        review_research(content=long_content, user_focus="")
        call_kwargs = mock_post.call_args[1]

    user_msg = _get_user_message(call_kwargs)
    import re as _re
    runs = _re.findall(r'D+', user_msg)
    longest_run = max((len(r) for r in runs), default=0)

    total_chars = _full_payload_chars(call_kwargs)
    est_tokens = total_chars // 3
    window_ok = est_tokens <= (16384 - 4096)

    passed = longest_run <= 8000
    _report(
        "review_research: content chars <= 8000",
        passed,
        f"content_chars_in_payload={longest_run} (limit=8000) | "
        f"total_payload_chars={total_chars} | est_tokens={est_tokens} | "
        f"fits_window={'YES' if window_ok else 'NO'}",
    )
    return longest_run


# ===========================================================================
# TEST 5 — generate_answer: context cap smoke test (existing, should pass)
# ===========================================================================
def test_generate_answer_context_cap():
    """generate_answer context must also stay within 6000 chars (existing fix)."""
    from generator import generate_answer

    big_docs = [FakeDoc("E" * 3000) for _ in range(5)]

    mock_resp = _make_mock_response(
        content='{"action":"chat","response":"ok","editor_content":null}'
    )
    with patch("generator.requests.post", return_value=mock_resp) as mock_post:
        generate_answer(
            query="What is this about?",
            retrieved_docs=big_docs,
            chat_history=None,
            editor_content=None,
            research_mode=False,
        )
        call_kwargs = mock_post.call_args[1]

    user_msg = _get_user_message(call_kwargs)
    context_marker = "=== บริบทจากเอกสาร ==="
    if context_marker in user_msg:
        idx = user_msg.index(context_marker) + len(context_marker)
        remainder = user_msg[idx:]
        next_block = remainder.find("===")
        context_block = remainder[:next_block].strip() if next_block != -1 else remainder.strip()
        actual_context_chars = len(context_block)
    else:
        actual_context_chars = 0

    total_chars = _full_payload_chars(call_kwargs)
    est_tokens = total_chars // 3
    window_ok = est_tokens <= (16384 - 2048)

    passed = actual_context_chars <= 6000
    _report(
        "generate_answer: context chars <= 6000 (existing cap, sanity check)",
        passed,
        f"context_chars={actual_context_chars} (limit=6000) | "
        f"total_payload_chars={total_chars} | est_tokens={est_tokens} | "
        f"fits_window={'YES' if window_ok else 'NO'}",
    )


# ===========================================================================
# TEST 6 — generate_answer_stream: context cap smoke test (existing, unchanged)
# ===========================================================================
def test_generate_answer_stream_context_cap():
    """generate_answer_stream context must stay within 6000 chars."""
    from generator import generate_answer_stream

    big_docs = [FakeDoc("F" * 3000) for _ in range(5)]

    # _call_api_stream uses stream=True; mock needs to handle iter_lines
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {"Content-Type": "application/json"}  # non-streaming path
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "test"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10},
    }
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    captured_kwargs = {}
    original_post = None

    def capturing_post(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return mock_resp

    with patch("generator.requests.post", side_effect=capturing_post):
        # Consume the generator so the post actually fires
        list(generate_answer_stream(
            query="test query",
            retrieved_docs=big_docs,
            chat_history=None,
        ))

    if not captured_kwargs:
        _report("generate_answer_stream: context chars <= 6000", False,
                "No request was captured — generator may not have been consumed")
        return

    stream_body = captured_kwargs.get("data", "")
    stream_flag_ok = isinstance(stream_body, str) and '"stream":true' in stream_body
    _report(
        "generate_answer_stream: request body uses JSON stream true",
        stream_flag_ok,
        "stream flag serialized as compact JSON" if stream_flag_ok else f"body={stream_body[:120]}",
    )

    user_msg = _get_user_message(captured_kwargs)
    context_marker = "=== บริบทจากเอกสาร ==="
    if context_marker in user_msg:
        idx = user_msg.index(context_marker) + len(context_marker)
        remainder = user_msg[idx:]
        next_block = remainder.find("===")
        context_block = remainder[:next_block].strip() if next_block != -1 else remainder.strip()
        actual_context_chars = len(context_block)
    else:
        actual_context_chars = 0

    total_chars = _full_payload_chars(captured_kwargs)
    est_tokens = total_chars // 3
    window_ok = est_tokens <= (16384 - 2048)

    passed = actual_context_chars <= 6000
    _report(
        "generate_answer_stream: context chars <= 6000 (existing cap, sanity check)",
        passed,
        f"context_chars={actual_context_chars} (limit=6000) | "
        f"total_payload_chars={total_chars} | est_tokens={est_tokens} | "
        f"fits_window={'YES' if window_ok else 'NO'}",
    )


# ===========================================================================
# TEST 7 — Determinism: generate_section called 3× same input → same payload size
# ===========================================================================
def test_generate_section_determinism():
    """Truncation must be deterministic: same input always produces same payload size."""
    from generator import generate_section

    big_docs = [FakeDoc("G" * 3000) for _ in range(5)]

    payload_chars_list = []
    for i in range(3):
        mock_resp = _make_mock_response()
        with patch("generator.requests.post", return_value=mock_resp) as mock_post:
            generate_section(
                topic="Determinism Topic",
                section_instruction="Write determinism section",
                retrieved_docs=big_docs,
                existing_content="",
            )
            call_kwargs = mock_post.call_args[1]
        payload_chars_list.append(_full_payload_chars(call_kwargs))

    all_equal = len(set(payload_chars_list)) == 1
    _report(
        "generate_section: deterministic truncation (3 runs identical payload size)",
        all_equal,
        f"payload_chars per run: {payload_chars_list}",
    )


# ===========================================================================
# TEST 8 — generate_section: existing_content tail is capped at 1500 chars
# ===========================================================================
def test_generate_section_existing_content_tail():
    """existing_content tail sent to API must be <= 1500 chars."""
    from generator import generate_section

    long_existing = "H" * 5000

    mock_resp = _make_mock_response()
    with patch("generator.requests.post", return_value=mock_resp) as mock_post:
        generate_section(
            topic="Tail Test Topic",
            section_instruction="Write next section",
            retrieved_docs=None,
            existing_content=long_existing,
        )
        call_kwargs = mock_post.call_args[1]

    user_msg = _get_user_message(call_kwargs)
    import re as _re
    runs = _re.findall(r'H+', user_msg)
    longest_run = max((len(r) for r in runs), default=0)

    passed = longest_run <= 1500
    _report(
        "generate_section: existing_content tail <= 1500 chars",
        passed,
        f"existing_content_tail_chars={longest_run} (limit=1500)",
    )


# ===========================================================================
# MAIN
# ===========================================================================
def main():
    print("=" * 70)
    print("TOKEN CAP VERIFICATION TESTS")
    print("All HTTP calls are mocked — no real API requests made.")
    print("=" * 70)
    print()

    # Run all tests
    test_generate_section_doc_cap()
    test_generate_section_per_doc_cap()
    test_generate_selection_edit_cap()
    test_review_research_cap()
    test_generate_answer_context_cap()
    test_generate_answer_stream_context_cap()
    test_generate_section_determinism()
    test_generate_section_existing_content_tail()

    # Summary
    print()
    print("=" * 70)
    passed = sum(1 for _, ok in _results if ok)
    failed = sum(1 for _, ok in _results if not ok)
    print(f"RESULTS: {passed}/{len(_results)} passed, {failed} failed")
    if failed:
        print()
        print("FAILED TESTS:")
        for name, ok in _results:
            if not ok:
                print(f"  - {name}")
    print("=" * 70)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
