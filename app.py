"""
Wijaiwai Research Workspace.
3-panel layout: Sidebar (Reference Vault) | Center (Workbench) | Right (Chat Assistant).
"""

import json
import os
import re
import tempfile

from tls_config import sanitize_tls_ca_bundle_env

sanitize_tls_ca_bundle_env()

import requests
import streamlit as st

import database
from auth import get_google_auth_url, handle_oauth_callback
from generator import (
    generate_answer,
    generate_answer_stream,
    generate_selection_edit,
    generate_insertion,
    generate_section,
    generate_section_from_docs,
    generate_section_stream,
    generate_section_from_docs_stream,
    generate_research_guide,
    is_small_talk,
    is_edit_intent,
)
from reviewer import review_research, analyze_papers_critically_stream


_THINK_PATTERN = re.compile(r'<think>(.*?)</think>', re.DOTALL)
WORK_DIR = os.path.join(os.path.dirname(__file__), "user_data")
INSUFFICIENT_VAULT_FALLBACK = (
    "No sufficient supporting paper was found in the Reference Vault. "
    "Upload or import a relevant paper before using this as a research-grounded answer."
)
REFERENCE_VAULT_SOURCE_TYPES = {"reference_document"}


def _is_reference_vault_doc(doc) -> bool:
    metadata = getattr(doc, "metadata", {}) or {}
    source_type = metadata.get("source_type", metadata.get("source", ""))
    return source_type in REFERENCE_VAULT_SOURCE_TYPES


def _filter_reference_vault_docs(docs):
    return [doc for doc in (docs or []) if _is_reference_vault_doc(doc)]


def _retrieve_reference_vault_docs(query: str, user_id: str, k: int = 5, **kwargs):
    try:
        from llamaindex_pinecone_rag import retrieve_reference_vault

        return retrieve_reference_vault(query, user_id, k=k, **kwargs)
    except Exception:
        return []


def _render_reference_vault_sources(docs, empty_caption=INSUFFICIENT_VAULT_FALLBACK):
    docs = _filter_reference_vault_docs(docs)
    if not docs:
        st.caption(empty_caption)
        return
    for i, doc in enumerate(docs, 1):
        metadata = doc.metadata or {}
        title = (
            metadata.get("paper_title")
            or metadata.get("filename")
            or metadata.get("source")
            or "Untitled paper"
        )
        author = metadata.get("author_display") or metadata.get("author") or "Unknown author"
        st.markdown(f"**Paper {i}:** {title}")
        st.caption(f"Reference Vault | {author}")
        preview = doc.page_content
        st.text(preview[:300] + "..." if len(preview) > 300 else preview)


# ── Editor Document helpers (SQLite-backed, per-user) ─────────────────────────

def save_work_to_db(user_id: str, name: str, title: str, content: str) -> str:
    """Save editor document to SQLite under user_id. Returns the doc name (used as key)."""
    from datetime import datetime
    safe_name = re.sub(r'[\\/*?:"<>|]', "_", name.strip())[:60]
    if not safe_name:
        safe_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    database.save_editor_document(user_id, safe_name, title, content)
    return safe_name


def save_work_to_db_new(user_id: str, name: str, title: str, content: str) -> str:
    """Save as a new editor document — appends timestamp to name to avoid collision."""
    from datetime import datetime
    safe_name = re.sub(r'[\\/*?:"<>|]', "_", name.strip())[:60]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_name = f"{safe_name}_{timestamp}"
    database.save_editor_document(user_id, unique_name, title, content)
    return unique_name


def list_work_docs(user_id: str) -> list:
    """List all editor documents for a user. Returns list of dicts."""
    return database.list_editor_documents(user_id)


# ── Legacy filesystem helpers (kept for Import from disk only) ─────────────────

def _ensure_work_dir():
    os.makedirs(WORK_DIR, exist_ok=True)


def load_work_from_file(filepath: str):
    """Load a .txt file from disk (used by Import only)."""
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()
    if raw.startswith("TITLE: ") and "\n---\n" in raw:
        header, _, content = raw.partition("\n---\n")
        title = header[len("TITLE: "):]
    else:
        title = os.path.splitext(os.path.basename(filepath))[0]
        content = raw
    return title, content


# ── Think-tag helpers ──────────────────────────────────────────────────────────

def parse_think_content(text: str):
    thinks = _THINK_PATTERN.findall(text)
    answer = _THINK_PATTERN.sub('', text).strip()
    think_text = '\n\n'.join(t.strip() for t in thinks) if thinks else ''
    return think_text, answer


def display_assistant_message(content: str):
    think_text, answer = parse_think_content(content)
    if think_text:
        with st.expander("💭 ความคิด (Thinking)", expanded=False):
            st.markdown(f'<div class="think-block">{think_text}</div>',
                        unsafe_allow_html=True)
    st.write(answer)


# ── Advisor review renderer ──────────────────────────────────────────────────

_REVIEW_TAG_STYLES = {
    "ต้องแก้ไข": {
        "bg": "#fef2f2", "border": "#fca5a5", "color": "#991b1b",
        "icon": "🔴", "label": "ต้องแก้ไข",
    },
    "ดีแล้ว": {
        "bg": "#f0fdf4", "border": "#86efac", "color": "#166534",
        "icon": "🟢", "label": "ดีแล้ว",
    },
    "คำแนะนำ": {
        "bg": "#fffbeb", "border": "#fcd34d", "color": "#92400e",
        "icon": "🟡", "label": "คำแนะนำ",
    },
}

_REVIEW_TAG_RE = re.compile(
    r'\[(ต้องแก้ไข|ดีแล้ว|คำแนะนำ)\]',
)


def _render_review_result(review_text: str):
    """Render advisor review with color-coded blocks, preserving markdown."""
    lines = review_text.split('\n')
    current_tag = None
    current_lines = []

    def _flush():
        nonlocal current_tag, current_lines
        content = '\n'.join(current_lines).strip()
        if not content:
            current_lines = []
            return
        if current_tag and current_tag in _REVIEW_TAG_STYLES:
            s = _REVIEW_TAG_STYLES[current_tag]
            # Render badge header, then markdown body separately so markdown is processed
            st.markdown(
                f'<div style="background:{s["bg"]};border-left:4px solid {s["border"]};'
                f'border-radius:8px 8px 0 0;padding:6px 14px;margin:6px 0 0 0;">'
                f'<strong style="color:{s["color"]};font-size:0.92rem;">'
                f'{s["icon"]} [{s["label"]}]</strong></div>',
                unsafe_allow_html=True,
            )
            st.markdown(content)
        else:
            # General text (overview / summary) — render as plain markdown
            st.markdown(content)
        current_lines = []

    for line in lines:
        m = _REVIEW_TAG_RE.search(line)
        if m:
            _flush()
            current_tag = m.group(1)
            # Remove the tag from the line text, keep the rest
            cleaned = _REVIEW_TAG_RE.sub('', line).strip(' -—:')
            if cleaned:
                current_lines.append(cleaned)
        else:
            current_lines.append(line)

    _flush()


# ============================================================================
# LOGIN PAGE — Google OAuth Splash Screen
# ============================================================================

def _show_login_page():
    """Render the login splash screen with Google Sign-In button."""
    import base64
    import pathlib

    # Load banner image
    _banner_path = pathlib.Path(__file__).parent / "pic" / "banner.jpeg"
    _b64 = ""
    if _banner_path.exists():
        _b64 = base64.b64encode(_banner_path.read_bytes()).decode()

    # Google SVG logo (inline)
    google_svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="20" height="20"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>'''

    auth_url = get_google_auth_url()

    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;600;700&family=Montserrat:wght@400;600;700&display=swap');
    [data-testid="stAppViewContainer"] {{
        background: linear-gradient(135deg, #f8faff 0%, #eef2ff 50%, #f0f4ff 100%);
    }}
    .login-container {{
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        min-height: 85vh;
        font-family: 'Montserrat', 'Prompt', sans-serif;
        animation: fadeIn 0.8s ease-out both;
    }}
    @keyframes fadeIn {{
        from {{ opacity: 0; transform: translateY(20px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}
    .login-card {{
        background: #ffffff;
        border-radius: 20px;
        box-shadow: 0 10px 40px rgba(0, 0, 0, 0.08);
        padding: 48px 40px;
        text-align: center;
        max-width: 440px;
        width: 100%;
    }}
    .login-banner {{
        max-width: 320px;
        border-radius: 12px;
        margin-bottom: 24px;
    }}
    .login-title {{
        font-family: 'Montserrat', sans-serif;
        font-size: 2.2rem;
        font-weight: 700;
        color: #1e293b;
        margin: 0 0 8px 0;
        letter-spacing: -0.02em;
    }}
    .login-subtitle {{
        font-family: 'Prompt', sans-serif;
        font-size: 1rem;
        font-weight: 300;
        color: #64748b;
        margin: 0 0 32px 0;
        letter-spacing: 0.02em;
    }}
    .google-btn {{
        display: inline-flex;
        align-items: center;
        gap: 12px;
        padding: 12px 32px;
        background: #ffffff;
        border: 2px solid #e2e8f0;
        border-radius: 12px;
        font-family: 'Montserrat', sans-serif;
        font-size: 0.95rem;
        font-weight: 600;
        color: #334155;
        text-decoration: none;
        transition: all 0.2s ease;
        cursor: pointer;
    }}
    .google-btn:hover {{
        border-color: #4285F4;
        box-shadow: 0 4px 16px rgba(66, 133, 244, 0.15);
        transform: translateY(-1px);
        color: #1e293b;
        text-decoration: none;
    }}
    .login-footer {{
        font-family: 'Prompt', sans-serif;
        font-size: 0.75rem;
        color: #94a3b8;
        margin-top: 24px;
    }}
    /* Hide Streamlit default elements on login page */
    header[data-testid="stHeader"] {{ display: none; }}
    #MainMenu {{ display: none; }}
    footer {{ display: none; }}
    </style>

    <div class="login-container">
        <div class="login-card">
            {"<img class='login-banner' src='data:image/jpeg;base64," + _b64 + "' />" if _b64 else ""}
            <h1 class="login-title">WijaiWai</h1>
            <p class="login-subtitle">AI Research Workspace</p>
            <a href="{auth_url}" class="google-btn">
                {google_svg}
                Sign in with Google
            </a>
            <p class="login-footer">Secure authentication powered by Google OAuth 2.0</p>
            <div style="margin-top:20px;padding:12px 16px;background:#f8faff;border-radius:10px;font-family:'Prompt',sans-serif;font-size:0.82rem;color:#475569;line-height:2;">
                <div>👥 จำนวนผู้ลงทะเบียนใช้งานในระบบ <strong style="color:#1e293b;">{database.get_total_users():,} คน</strong></div>
                <div>🔢 มีการใช้ token ไปแล้ว &nbsp; input: <strong style="color:#1e293b;">{database.get_total_token_usage()['input_tokens']:,}</strong> &nbsp;|&nbsp; output: <strong style="color:#1e293b;">{database.get_total_token_usage()['output_tokens']:,}</strong></div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)


# ============================================================================
# MAIN APP
# ============================================================================

def main():
    from PIL import Image as _PILImage
    _logo_img = _PILImage.open(
        os.path.join(os.path.dirname(__file__), "pic", "logo.jpeg")
    )
    st.set_page_config(
        page_title="WijaiWai",
        page_icon=_logo_img,
        layout="wide"
    )

    # ── Initialize session state for auth ─────────────────────────────────────
    if "user" not in st.session_state:
        st.session_state.user = None

    # ── Handle OAuth callback ─────────────────────────────────────────────────
    query_params = st.query_params
    auth_code = query_params.get("code")

    if auth_code and st.session_state.user is None:
        try:
            user_info = handle_oauth_callback(auth_code)
            st.session_state.user = user_info
            # Clear the code from URL
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Login failed: {str(e)}")
            st.query_params.clear()
            st.stop()

    # ── Login gate: show login page if not authenticated ──────────────────────
    if st.session_state.user is None:
        _show_login_page()
        st.stop()

    # ── User is authenticated — proceed with main app ─────────────────────────
    user = st.session_state.user
    user_id = user["id"]

    # ── Session state defaults ─────────────────────────────────────────────────
    defaults = {
        "processed_docs": [],
        "messages": [],
        "total_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_cost_thb": 0.0,
        "work_title_val": "",
        "work_content_val": "",
        "work_current_file": None,
        "_editor_restored": False,
        "work_load_select": None,
        "work_save_dialog": None,
        "work_save_dialog_name": "",
        "work_import_open": False,
        "work_export_open": False,
        "_app_initialized": False,
        "_research_mode": False,
        "ai_edit_undo_stack": [],
        "ai_edit_redo_stack": [],
        "review_result": None,
        "review_expanded": True,
        "review_retrieved_docs": None,
        "section_retrieved_docs": None,
        "section_doc_result": None,
        "section_llm_result": None,
        "section_llm_think": "",
        "section_llm_docs": [],
        "compare_result": None,
        "compare_expanded": True,
        "compare_retrieved_docs": None,
        "compare_selected_papers": [],
        "citation_result": None,
        "citation_sources": [],
        "citation_cited_content": None,
        "research_guide_result": None,
        "research_guide_retrieved_docs": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # ── Auto-restore last editor document on login ────────────────────────────
    if not st.session_state.get("_editor_restored"):
        st.session_state._editor_restored = True
        _saved_docs = database.list_editor_documents(user_id)
        if _saved_docs:
            _last = _saved_docs[0]  # newest-first order
            st.session_state.work_title_val = _last["title"] or ""
            st.session_state.work_content_val = _last["content"] or ""
            st.session_state.work_current_file = _last["name"]

    # ── Banner image (used by splash and top banner) ──────────────────────────
    import base64, pathlib
    _banner_path = pathlib.Path(__file__).parent / "pic" / "banner.jpeg"
    _b64 = base64.b64encode(_banner_path.read_bytes()).decode()

    # ── Loading screen (first run only) ───────────────────────────────────────
    if not st.session_state._app_initialized:

        # ── Splash screen: Logo + App name ────────────────────────────────
        splash = st.empty()
        with splash.container():
            st.markdown(f"""
            <style>
            @import url('https://fonts.googleapis.com/css2?family=Prompt:wght@400;600;700&family=Montserrat:wght@400;600;700&display=swap');
            @keyframes fadeIn {{
                from {{ opacity: 0; }}
                to {{ opacity: 1; }}
            }}
            .splash-screen {{
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 92vh;
                background: #f8faff;
                border-radius: 16px;
                animation: fadeIn 0.8s ease-out both;
                font-family: 'Montserrat', 'Prompt', sans-serif;
            }}
            .splash-banner {{
                max-width: 100%;
                max-height: 80vh;
                object-fit: contain;
                border-radius: 12px;
            }}
            </style>
            <div class="splash-screen">
                <img class="splash-banner" src="data:image/jpeg;base64,{_b64}" />
            </div>
            """, unsafe_allow_html=True)

        import time
        time.sleep(2.5)
        splash.empty()

        # ── Loading progress screen ──────────────────────────────────────
        loading = st.empty()
        with loading.container():
            st.markdown(f"""
            <style>
            @import url('https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;600&family=Montserrat:wght@400;600&display=swap');
            @keyframes pulse {{
                0%, 100% {{ opacity: 1; }}
                50% {{ opacity: 0.4; }}
            }}
            .loading-screen {{
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                min-height: 80vh;
                text-align: center;
                font-family: 'Montserrat', 'Prompt', sans-serif;
            }}
            .loading-subtitle {{
                font-size: 1rem;
                font-family: 'Prompt', sans-serif;
                font-weight: 300;
                color: #6b7280;
                margin-bottom: 2rem;
                letter-spacing: 0.03em;
            }}
            </style>
            <div class="loading-screen">
                <img src="data:image/jpeg;base64,{_b64}" style="max-width:320px; border-radius:12px; margin-bottom:1rem;" />
                <div class="loading-subtitle">กำลังเตรียมระบบ...</div>
            </div>
            """, unsafe_allow_html=True)

            progress = st.progress(0, text="เริ่มต้นระบบ...")

            # Pinecone and embedding clients are initialized lazily by upload/RAG actions.
            progress.progress(90, text="Preparing workspace...")

            # Step 3: Finalize
            progress.progress(100, text="✅ พร้อมใช้งาน!")

            import time
            time.sleep(0.8)

        # Clear loading screen and mark as initialized
        loading.empty()
        st.session_state._app_initialized = True
        st.rerun()

    # Apply pending editor content BEFORE any widget is rendered
    for widget_key, pending_key in [
        ("work_title_input", "_pending_work_title"),
        ("work_content_input", "_pending_work_content"),
    ]:
        if pending_key in st.session_state:
            st.session_state[widget_key] = st.session_state.pop(pending_key)

    # ── Styles ────────────────────────────────────────────────────────────────
    st.markdown("""
    <style>
    /* ══════════════════════════════════════════════════════════════════
       GOOGLE FONTS IMPORTS
       Prompt  — Thai UI font
       Montserrat — English / Latin font
       Sarabun — editor & text inputs
    ══════════════════════════════════════════════════════════════════ */
    @import url('https://fonts.googleapis.com/css2?family=Prompt:ital,wght@0,300;0,400;0,500;0,600;0,700&family=Montserrat:ital,wght@0,400;0,500;0,600;0,700&family=Sarabun:ital,wght@0,300;0,400;0,500;0,600&display=swap');

    /* ── Base: Montserrat (Latin) + Prompt (Thai) ────────────────────
       IMPORTANT: do NOT set on html/body — that cascades !important
       into Streamlit's Material Symbols icon spans and breaks them.
       Target only known text-bearing elements instead.
    ─────────────────────────────────────────────────────────────── */
    .stApp, .stMarkdown, .stText,
    p, li, h1, h2, h3, h4, h5, h6,
    .stChatMessage, .stChatMessage p,
    .stCaption, label,
    .stTabs [data-baseweb="tab"],
    section[data-testid="stSidebar"] {
        font-family: 'Montserrat', 'Prompt', sans-serif !important;
    }

    /* ── Expander content: ensure font applies inside collapsed/expanded blocks ─
       Streamlit expander content renders in its own stacking context and
       can miss the base rule above on some Streamlit versions.
    ─────────────────────────────────────────────────────────────── */
    .streamlit-expanderContent .stMarkdown,
    .streamlit-expanderContent p,
    .streamlit-expanderContent li,
    .streamlit-expanderContent h1,
    .streamlit-expanderContent h2,
    .streamlit-expanderContent h3,
    .streamlit-expanderContent h4,
    [data-testid="stExpander"] .stMarkdown,
    [data-testid="stExpander"] p,
    [data-testid="stExpander"] li {
        font-family: 'Montserrat', 'Prompt', sans-serif !important;
        font-size: 1rem;
        line-height: 1.75;
    }

    /* ── Explicitly restore Material Symbols icon font ────────────
       Streamlit renders icons as <span class="material-symbols-rounded">
       Restoring here beats any inherited override.
    ─────────────────────────────────────────────────────────────── */
    [class*="material-symbols"],
    [class*="material-icons"],
    .material-symbols-rounded,
    .material-symbols-outlined,
    .material-symbols-sharp,
    .material-icons,
    .material-icons-outlined {
        font-family: 'Material Symbols Rounded', 'Material Icons', sans-serif !important;
        font-size: inherit;
    }

    :root {
        --font-ui: 'Montserrat', 'Prompt', sans-serif;
        --font-editor: 'Sarabun', 'Prompt', sans-serif;
    }

    body { font-size: 14px; }

    h1 { font-size: 1.45rem !important; font-weight: 700 !important; }
    h2 { font-size: 1.2rem  !important; font-weight: 600 !important; }
    h3 { font-size: 1.05rem !important; font-weight: 600 !important; }

    section[data-testid="stSidebar"] {
        width: 22vw !important;
        min-width: 240px !important;
        max-width: 320px !important;
    }
    section[data-testid="stSidebar"] > div:first-child {
        width: 22vw !important;
        min-width: 240px !important;
        max-width: 320px !important;
        padding-top: 1.5rem;
    }

    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] .stCaption {
        font-size: 0.82rem !important;
    }
    section[data-testid="stSidebar"] strong,
    section[data-testid="stSidebar"] b {
        font-size: 0.86rem !important;
    }

    .stChatMessage p, .stChatMessage span {
        font-size: 0.9rem !important;
        line-height: 1.75 !important;
    }
    .stChatMessage .stCaption {
        font-size: 0.75rem !important;
    }

    textarea,
    textarea[data-testid="stTextArea"],
    .stTextArea textarea,
    input[type="text"],
    .stTextInput input,
    input[data-testid="stTextInput"],
    .stChatInputContainer textarea,
    div[data-testid="stChatInput"] textarea {
        font-family: 'Sarabun', 'Prompt', sans-serif !important;
        font-size: 14px !important;
        line-height: 1.8 !important;
    }

    .stTextArea textarea {
        font-size: 17px !important;
        line-height: 1.9 !important;
        letter-spacing: 0.01em;
    }

    .stTextInput input {
        font-size: 14px !important;
        font-weight: 500 !important;
    }

    .think-block {
        background-color: #f0f4f8;
        border-left: 4px solid #90a4ae;
        border-radius: 6px;
        padding: 8px 12px;
        margin-bottom: 8px;
        color: #546e7a;
        font-style: italic;
        font-size: 0.88em;
        white-space: pre-wrap;
        font-family: var(--font-ui) !important;
    }

    .section-header {
        font-size: 1.1rem;
        font-weight: 600;
        margin-bottom: 0.5rem;
        color: #1f2937;
        font-family: var(--font-ui) !important;
    }

    div[data-testid="stColumns"] > div[data-testid="column"]:last-child p,
    div[data-testid="stColumns"] > div[data-testid="column"]:last-child span,
    div[data-testid="stColumns"] > div[data-testid="column"]:last-child label,
    div[data-testid="stColumns"] > div[data-testid="column"]:last-child .stMarkdown {
        font-size: 0.88rem !important;
    }
    div[data-testid="stColumns"] > div[data-testid="column"]:last-child [class*="material-symbols"],
    div[data-testid="stColumns"] > div[data-testid="column"]:last-child [class*="material-icons"] {
        font-family: 'Material Symbols Rounded', 'Material Icons', sans-serif !important;
        font-size: inherit !important;
    }

    section[data-testid="stSidebar"] .stMarkdown h3 {
        margin-top: 0;
        margin-bottom: 0.4rem;
    }

    div[data-testid="stColumns"] + div[data-testid="stColumns"] {
        margin-top: -0.5rem;
    }

    .stButton > button,
    .stDownloadButton > button {
        font-family: 'Montserrat', 'Prompt', sans-serif !important;
        font-size: 0.82rem !important;
        font-weight: 500 !important;
        padding-top: 0.3rem;
        padding-bottom: 0.3rem;
    }

    .stSelectbox div[data-baseweb="select"] *,
    .stRadio label span {
        font-family: var(--font-ui) !important;
        font-size: 0.86rem !important;
    }

    .stCaption {
        font-size: 0.75rem !important;
        color: #6b7280;
    }

    [data-testid="stMetricValue"] {
        font-size: 1.1rem !important;
        font-weight: 600 !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.75rem !important;
    }

    /* ── Hide Streamlit's built-in "Limit 200MB per file" uploader hint ──
       We show our own custom caption ("จำกัดสูงสุด 5 ไฟล์ · 5 MB ต่อไฟล์")
       so the default helper text would create a conflicting double-caption.
    ─────────────────────────────────────────────────────────────────────── */
    [data-testid="stFileUploaderDropzoneInstructions"] div small,
    [data-testid="stFileUploaderDropzoneInstructions"] small,
    [data-testid="stFileUploaderDropzone"] small {
        display: none !important;
    }
    </style>
    """, unsafe_allow_html=True)

    # ============================================================================
    # LEFT PANEL: Reference Vault
    # ============================================================================
    with st.sidebar:
        col_user, col_logout = st.columns([4, 1])
        with col_user:
            user_display = user.get("name", user.get("email", "User"))
            picture_url = user.get("picture", "")
            if picture_url:
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">'
                    f'<img src="{picture_url}" style="width:28px;height:28px;border-radius:50%;"/>'
                    f'<span style="font-size:0.85rem;font-weight:500;color:#374151;">{user_display}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(f"**{user_display}**")
        with col_logout:
            if st.button("Logout", key="logout_btn", help="Logout"):
                st.session_state.user = None
                st.session_state._app_initialized = False
                for key in list(st.session_state.keys()):
                    if key != "user":
                        del st.session_state[key]
                st.rerun()

        st.divider()
        st.markdown("""
        <div style="font-size:1.35rem;font-weight:700;color:#1f2937;padding:0.25rem 0 0.4rem 0;">
            Reference Vault
        </div>""", unsafe_allow_html=True)

        st.markdown("**Upload paper**")
        _uploaded_file = st.file_uploader(
            "Select PDF paper",
            type=["pdf"],
            accept_multiple_files=False,
            key="file_uploader_sidebar",
            help="PDF-first upload for the Docling target pipeline.",
        )
        st.caption("PDF-first paper uploads only. TXT/DOC/DOCX generic knowledge uploads are outside active v3 scope.")
        uploaded_files = [_uploaded_file] if _uploaded_file is not None else []

        if uploaded_files:
            st.caption(f"1 paper selected: {uploaded_files[0].name}")
            if st.button("Process paper", type="primary", key="process_doc_btn", use_container_width=True):
                _MAX_DOCS = 5
                _MAX_FILE_BYTES = 15 * 1024 * 1024
                _current_doc_count = len([
                    d for d in database.list_reference_vault_documents(user_id)
                    if d.get("status") != "archived"
                ])
                _slots_remaining = _MAX_DOCS - _current_doc_count

                _valid_files = []
                for _uf in uploaded_files:
                    _file_size = len(_uf.getvalue())
                    ext = os.path.splitext(_uf.name)[1].lower()
                    if ext != '.pdf':
                        st.error(f"{_uf.name}: only PDF papers are active in the v3 Reference Vault UI.")
                    elif _file_size == 0:
                        st.error(f"{_uf.name}: empty file skipped.")
                    elif _file_size > _MAX_FILE_BYTES:
                        st.error(f"{_uf.name}: file is too large ({_file_size / 1024 / 1024:.1f} MB). Maximum is 15 MB.")
                    else:
                        _valid_files.append(_uf)

                if _slots_remaining <= 0:
                    st.error(f"Reference Vault limit reached ({_MAX_DOCS} papers). Delete an old paper before uploading a new one.")
                    _valid_files = []
                elif len(_valid_files) > _slots_remaining:
                    st.warning(f"Only {_slots_remaining} more paper(s) can be added.")
                    _valid_files = _valid_files[:_slots_remaining]

                new_doc_entries = []

                with st.spinner(f"Processing {len(_valid_files)} paper(s) for Reference Vault..."):
                    from docling_ingestion import ingest_uploaded_pdf_with_docling
                    from llamaindex_pinecone_rag import index_docling_result

                    for uploaded_file in _valid_files:
                        tmp_path = None
                        try:
                            ext = os.path.splitext(uploaded_file.name)[1].lower()
                            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp_file:
                                tmp_file.write(uploaded_file.getvalue())
                                tmp_path = tmp_file.name

                            docling_result = ingest_uploaded_pdf_with_docling(
                                tmp_path,
                                filename=uploaded_file.name,
                                user_id=user_id,
                            )
                            index_result = index_docling_result(docling_result)
                            doc_id = docling_result.document_id
                            new_doc_entries.append({
                                "name": uploaded_file.name,
                                "doc_id": doc_id,
                                "project_id": docling_result.project_id,
                                "status": "active",
                                "chunks": index_result.get("chunk_count", 0),
                            })
                        except Exception as e:
                            st.error(f"{uploaded_file.name}: {str(e)}")
                        finally:
                            if tmp_path and os.path.exists(tmp_path):
                                os.unlink(tmp_path)

                    if new_doc_entries:
                        st.session_state.messages = []
                        st.session_state.total_tokens = 0
                        st.session_state.input_tokens = 0
                        st.session_state.output_tokens = 0
                        st.session_state.total_cost_thb = 0.0
                        _indexed_chunks = sum(entry.get("chunks", 0) for entry in new_doc_entries)
                        st.success(
                            "PDF converted to Markdown and saved to Reference Vault. "
                            f"Indexed {_indexed_chunks} Reference Vault chunk(s)."
                        )

        st.divider()
        st.markdown("**OpenAlex metadata discovery**")
        st.text_input(
            "Search OpenAlex",
            placeholder="Search by title, DOI, author, or topic",
            key="openalex_search_input",
            disabled=True,
        )
        st.button(
            "Search OpenAlex",
            key="openalex_search_btn",
            use_container_width=True,
            disabled=True,
        )
        st.info("OpenAlex import is unavailable in this UI phase because backend search/import hooks are not complete. Imported metadata will appear here as metadata_only when the backend hook is available.")

        st.divider()
        st.markdown("**Vault paper list**")
        try:
            _vault_docs = database.list_reference_vault_documents(user_id)
        except Exception:
            _vault_docs = []

        if _vault_docs:
            st.caption(f"{len(_vault_docs)} Reference Vault record(s)")
            for _di, _rv in enumerate(_vault_docs):
                _paper_name = _rv.get("paper_name") or _rv.get("filename") or "Untitled paper"
                _author = _rv.get("author_display") or "Unknown author"
                _status = _rv.get("status") or "active"
                col_info, col_del = st.columns([5, 1])
                with col_info:
                    st.markdown(f"**{_paper_name}**")
                    st.caption(f"{_author} | status: {_status}")
                    if _status == "metadata_only" or (_rv.get("source_type") == "openalex" and not _rv.get("storage_path")):
                        st.warning("Metadata only: no uploaded/full-text paper is available for evidence-backed RAG or citation.")
                with col_del:
                    if _status != "archived" and st.button("Archive", key=f"archive_rv_doc_{_di}", help="Archive this paper"):
                        database.delete_reference_vault_document(_rv["document_id"], user_id)
                        st.rerun()
        else:
            st.info("No papers in the Reference Vault yet. Upload a PDF paper or import OpenAlex metadata when available.")


    # ============================================================================
    # MAIN CONTENT: Center (Workbench) | Right (Chat Assistant)
    # ============================================================================
    col_center, col_right = st.columns([3, 2], gap="large")

    # ── Center: Workbench ─────────────────────────────────────────────────────
    with col_center:
        st.markdown("""
        <div style="font-size:17px;font-weight:700;color:#1f2937;padding:0.25rem 0 0.2rem 0;">
            📝 Workbench
        </div>""", unsafe_allow_html=True)

        _current_file_preview = st.session_state.get("work_current_file")
        if _current_file_preview:
            st.markdown(
                f'<div style="font-family:\'Sarabun\',sans-serif;font-size:15px;'
                f'color:#1d4ed8;font-weight:600;padding:2px 0 10px 0;">'
                f'📄 กำลังทำงานกับไฟล์: <span style="font-style:italic;">{_current_file_preview}</span></div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown('<div style="padding-bottom:10px;"></div>', unsafe_allow_html=True)

        work_title = st.text_input(
            "Title",
            value=st.session_state.work_title_val,
            placeholder="Enter a title for your research...",
            key="work_title_input"
        )
        work_content = st.text_area(
            "Content",
            value=st.session_state.work_content_val,
            placeholder="Start writing your research here...",
            height=400,
            key="work_content_input"
        )

        # ── Character counter and limit warning ───────────────────────────
        _EDITOR_CHAR_LIMIT = 50_000
        _char_count = len(work_content)
        _char_pct = _char_count / _EDITOR_CHAR_LIMIT
        if _char_count >= _EDITOR_CHAR_LIMIT:
            st.error(
                f"เกินขีดจำกัด {_EDITOR_CHAR_LIMIT:,} ตัวอักษร "
                f"({_char_count:,}/{_EDITOR_CHAR_LIMIT:,}) — "
                "AI อาจทำงานไม่ถูกต้อง กรุณาลดเนื้อหา"
            )
        elif _char_pct >= 0.85:
            st.warning(
                f"⚠️ ใกล้ถึงขีดจำกัด: {_char_count:,}/{_EDITOR_CHAR_LIMIT:,} ตัวอักษร "
                f"({_char_pct * 100:.0f}%)"
            )
        else:
            st.caption(f"ตัวอักษร: {_char_count:,} / {_EDITOR_CHAR_LIMIT:,}")

        st.markdown(
            "<style>div[data-testid='stButton']:has(button[kind='secondary']#clear_content_btn) button,"
            "div[data-testid='stButton'] button[key='clear_content_btn'] { white-space: nowrap; }</style>",
            unsafe_allow_html=True,
        )
        _ccol1, _ccol2 = st.columns([5, 2])
        with _ccol2:
            if st.button("🗑️ ล้างเนื้อหา", key="clear_content_btn", help="ล้างเนื้อหาทั้งหมดในตัวแก้ไข", use_container_width=True):
                st.session_state.work_content_val = ""
                st.session_state["_pending_work_content"] = ""
                st.rerun()

        # ── Research Guide Section ────────────────────────────────────────
        with st.expander("🧭 แนวทางการวิจัย (Research Guide)", expanded=False):
            guide_topic = st.text_input(
                "คุณอยากวิจัยเกี่ยวกับอะไร?",
                value=work_title if work_title.strip() else "",
                placeholder="เช่น ผลกระทบของ AI ต่อการศึกษาไทย, การพัฒนาหลักสูตรออนไลน์",
                key="guide_topic_input",
            )
            if st.button("🧭 สร้างแนวทางการวิจัย", type="primary",
                         key="research_guide_btn", use_container_width=True):
                if not guide_topic.strip():
                    st.warning("⚠️ กรุณาระบุหัวข้อวิจัย")
                else:
                    with st.spinner("🧭 กำลังสร้างแนวทางการวิจัย..."):
                        try:
                            _guide_docs = _retrieve_reference_vault_docs(
                                guide_topic.strip(), user_id, k=5,
                            )
                            st.session_state.research_guide_retrieved_docs = _guide_docs
                            _guide_text, _guide_input_tokens, _guide_output_tokens = (
                                generate_research_guide(
                                    topic=guide_topic.strip(),
                                    retrieved_docs=_guide_docs,
                                    existing_content=work_content,
                                )
                            )
                            database.record_token_usage(
                                user_id, _guide_input_tokens, _guide_output_tokens,
                                "research_guide",
                            )
                            st.session_state.research_guide_result = _guide_text
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ เกิดข้อผิดพลาด: {str(e)}")

        # ── Section-by-Section: สร้างเนื้อหาวิจัย ────────────────────────
        with st.expander("📝 สร้างเนื้อหาวิจัย", expanded=False):
            sec_topic = st.text_input(
                "หัวข้อของเนื้อหาที่ต้องการสร้าง",
                value=work_title if work_title.strip() else "",
                placeholder="เช่น ผลกระทบของ AI ต่อการศึกษา",
                key="sec_topic_input",
            )
            _gen_source = st.radio(
                "แหล่งข้อมูล",
                ["ความรู้ AI", "เอกสารที่เลือก"],
                horizontal=True,
                key="sec_gen_source",
                label_visibility="collapsed",
            )
            _input_mode = st.radio(
                "วิธีระบุส่วนที่ต้องการเขียน",
                ["เลือกจาก preset", "กำหนดเอง"],
                horizontal=True,
                key="sec_input_mode",
                label_visibility="collapsed",
            )

            if _input_mode == "เลือกจาก preset":
                sec_presets = [
                    "บทที่ 1: บทนำ — ที่มาและความสำคัญ วัตถุประสงค์ ขอบเขต",
                    "บทที่ 2: ทบทวนวรรณกรรม — ทฤษฎีและงานวิจัยที่เกี่ยวข้อง",
                    "บทที่ 3: วิธีดำเนินการวิจัย — ประชากร เครื่องมือ การเก็บข้อมูล",
                    "บทที่ 4: ผลการวิจัย — นำเสนอข้อมูลและการวิเคราะห์",
                    "บทที่ 5: สรุป อภิปราย และข้อเสนอแนะ",
                ]
                preset_choice = st.radio(
                    "เลือก preset",
                    sec_presets,
                    key="sec_preset_select",
                    label_visibility="collapsed",
                )
                sec_instruction = ""
            else:
                sec_instruction = st.text_area(
                    "ส่วนที่ต้องการเขียน",
                    placeholder=(
                        "กำหนดหัวข้อและรูปแบบเนื้อหาได้อิสระ ไม่จำกัดเฉพาะงานวิจัย\n"
                        "เช่น บทความข่าว, บล็อก, ตัวอย่างการนำงานวิจัยมาปรับใช้"
                    ),
                    height=80,
                    key="sec_instruction_input",
                )
                preset_choice = None

            if _gen_source == "เอกสารที่เลือก":
                _vault_docs_for_section = [
                    d for d in database.list_reference_vault_documents(user_id, status="active")
                    if d.get("docling_status") == "succeeded"
                ]
                _doc_options = {
                    f"{d.get('paper_name') or d.get('filename') or 'Untitled paper'} ({d['document_id'][:8]})": d["document_id"]
                    for d in _vault_docs_for_section
                }
                if _doc_options:
                    _selected_docs = st.multiselect(
                        "เลือกเอกสารจาก Reference Vault",
                        options=list(_doc_options.keys()),
                        key="sec_selected_docs",
                        placeholder="เลือกอย่างน้อย 1 เอกสาร...",
                    )
                    _selected_doc_ids = [_doc_options[label] for label in _selected_docs]
                else:
                    st.caption("ยังไม่มีเอกสารใน Reference Vault")
                    _selected_docs = []
                    _selected_doc_ids = []
            else:
                _selected_docs = []
                _selected_doc_ids = []

            sec_generate = st.button(
                "🚀 สร้างเนื้อหา",
                key="sec_generate_btn",
                type="primary",
                use_container_width=True,
            )

            if sec_generate:
                final_instruction = preset_choice if preset_choice else sec_instruction.strip()

                if not sec_topic.strip():
                    st.warning("⚠️ กรุณาระบุหัวข้อเอกสาร")
                elif not final_instruction:
                    st.warning("⚠️ กรุณาระบุส่วนที่ต้องการเขียน หรือเลือกจาก preset")
                elif _gen_source == "ความรู้ AI":
                    # ── LLM mode: pure AI knowledge, no RAG ──────────────────
                    st.markdown("### ✍️ กำลังสร้างเนื้อหา...")
                    try:
                        _streamed = st.write_stream(
                            generate_section_stream(
                                topic=sec_topic.strip(),
                                section_instruction=final_instruction,
                                existing_content=work_content,
                            )
                        )
                        _approx_in = max(1, (len(sec_topic) + len(final_instruction) + len(work_content[-1500:])) // 4)
                        _approx_out = max(1, len(str(_streamed)) // 4)
                        st.session_state.total_tokens += _approx_in + _approx_out
                        st.session_state.input_tokens += _approx_in
                        st.session_state.output_tokens += _approx_out
                        database.record_token_usage(user_id, _approx_in, _approx_out, "generate_section")
                        st.session_state.section_llm_result = str(_streamed)
                        st.session_state.section_llm_think = ""
                        st.session_state.section_llm_docs = []
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ เกิดข้อผิดพลาดในการสร้างเนื้อหา: {str(e)}")
                else:
                    # ── Document mode: retrieve from selected docs then generate ─
                    if not _selected_docs:
                        st.warning("⚠️ กรุณาเลือกเอกสารอย่างน้อย 1 รายการ")
                    else:
                        retrieved = _retrieve_reference_vault_docs(
                            f"{sec_topic} {final_instruction}",
                            user_id,
                            k=9,
                            document_ids=_selected_doc_ids,
                        )
                        # Deduplicate by content[:100] fingerprint, cap at 9 total
                        seen = set()
                        deduped = []
                        for d in retrieved:
                            fp = d.page_content[:100]
                            if fp not in seen:
                                seen.add(fp)
                                deduped.append(d)
                        retrieved = deduped[:9]
                        st.session_state.section_retrieved_docs = retrieved

                        st.markdown("### ✍️ กำลังเขียนจากเอกสาร...")
                        try:
                            _streamed = st.write_stream(
                                generate_section_from_docs_stream(
                                    topic=sec_topic.strip(),
                                    section_instruction=final_instruction,
                                    retrieved_docs=retrieved,
                                    existing_content=work_content,
                                )
                            )
                            _approx_in = max(1, sum(len(d.page_content) for d in retrieved) // 4)
                            _approx_out = max(1, len(str(_streamed)) // 4)
                            st.session_state.total_tokens += _approx_in + _approx_out
                            st.session_state.input_tokens += _approx_in
                            st.session_state.output_tokens += _approx_out
                            database.record_token_usage(user_id, _approx_in, _approx_out, "generate_section_from_docs")
                            st.session_state.section_doc_result = str(_streamed)
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ เกิดข้อผิดพลาดในการสร้างเนื้อหา: {str(e)}")

        # ── Document-mode output display ──────────────────────────────────────
        if st.session_state.get("section_doc_result"):
            _sdoc_think, _sdoc_body = parse_think_content(st.session_state.section_doc_result)
            with st.expander("📄 เนื้อหาที่สร้างจากเอกสาร", expanded=True):
                st.markdown(_sdoc_body)
                if _sdoc_think:
                    with st.expander("💭 ความคิด (Thinking)", expanded=False):
                        st.markdown(
                            f'<div class="think-block">{_sdoc_think}</div>',
                            unsafe_allow_html=True,
                        )
                _sdoc_docs = _filter_reference_vault_docs(st.session_state.get("section_retrieved_docs"))
                _sdoc_ref_label = f"Reference Vault sources - {len(_sdoc_docs)} paper(s)" if _sdoc_docs else "Reference Vault sources - insufficient evidence"
                with st.expander(_sdoc_ref_label, expanded=False):
                    _render_reference_vault_sources(_sdoc_docs)
            _sdb1, _sdb2, _sdb3 = st.columns(3)
            with _sdb1:
                if st.button("📥 Replace Content in Editor", key="sec_doc_replace_btn", use_container_width=True):
                    _cur = st.session_state.get("work_content_val", "")
                    st.session_state.ai_edit_undo_stack.append(_cur)
                    if len(st.session_state.ai_edit_undo_stack) > 20:
                        st.session_state.ai_edit_undo_stack.pop(0)
                    st.session_state.ai_edit_redo_stack = []
                    st.session_state["_pending_work_content"] = _sdoc_body
                    st.session_state.work_content_val = _sdoc_body
                    st.session_state.section_doc_result = None
                    st.session_state.section_retrieved_docs = None
                    st.rerun()
            with _sdb2:
                if st.button("➕ Append Content in Editor", key="sec_doc_append_btn", use_container_width=True):
                    _cur = st.session_state.get("work_content_val", "")
                    _sep = "\n\n" if _cur.strip() else ""
                    _new = _cur + _sep + _sdoc_body
                    st.session_state.ai_edit_undo_stack.append(_cur)
                    if len(st.session_state.ai_edit_undo_stack) > 20:
                        st.session_state.ai_edit_undo_stack.pop(0)
                    st.session_state.ai_edit_redo_stack = []
                    st.session_state["_pending_work_content"] = _new
                    st.session_state.work_content_val = _new
                    st.session_state.section_doc_result = None
                    st.session_state.section_retrieved_docs = None
                    st.rerun()
            with _sdb3:
                if st.button("❌ Cancel", key="sec_doc_cancel_btn", use_container_width=True):
                    st.session_state.section_doc_result = None
                    st.session_state.section_retrieved_docs = None
                    st.rerun()

        # ── LLM-mode output display ────────────────────────────────────────────
        if st.session_state.get("section_llm_result"):
            _sllm_think, _sllm_body = parse_think_content(st.session_state.section_llm_result)
            with st.expander("📝 เนื้อหาที่สร้าง", expanded=True):
                st.markdown(_sllm_body)
                if _sllm_think:
                    with st.expander("💭 ความคิด (Thinking)", expanded=False):
                        st.markdown(
                            f'<div class="think-block">{_sllm_think}</div>',
                            unsafe_allow_html=True,
                        )
            _slb1, _slb2, _slb3 = st.columns(3)
            with _slb1:
                if st.button("📥 Replace Content in Editor", key="sec_llm_replace_btn", use_container_width=True):
                    _cur = st.session_state.get("work_content_val", "")
                    st.session_state.ai_edit_undo_stack.append(_cur)
                    if len(st.session_state.ai_edit_undo_stack) > 20:
                        st.session_state.ai_edit_undo_stack.pop(0)
                    st.session_state.ai_edit_redo_stack = []
                    st.session_state["_pending_work_content"] = _sllm_body
                    st.session_state.work_content_val = _sllm_body
                    st.session_state.section_llm_result = None
                    st.rerun()
            with _slb2:
                if st.button("➕ Append Content in Editor", key="sec_llm_append_btn", use_container_width=True):
                    _cur = st.session_state.get("work_content_val", "")
                    _sep = "\n\n" if _cur.strip() else ""
                    _new = _cur + _sep + _sllm_body
                    st.session_state.ai_edit_undo_stack.append(_cur)
                    if len(st.session_state.ai_edit_undo_stack) > 20:
                        st.session_state.ai_edit_undo_stack.pop(0)
                    st.session_state.ai_edit_redo_stack = []
                    st.session_state["_pending_work_content"] = _new
                    st.session_state.work_content_val = _new
                    st.session_state.section_llm_result = None
                    st.rerun()
            with _slb3:
                if st.button("❌ Cancel", key="sec_llm_cancel_btn", use_container_width=True):
                    st.session_state.section_llm_result = None
                    st.rerun()

        # ── Advisor Review Section ────────────────────────────────────────
        with st.expander("🎓 ตรวจงานโดย AI", expanded=False):
            review_focus = st.text_area(
                "อยากตรวจอะไรเป็นพิเศษ? (optional)",
                value="",
                placeholder="เช่น ตรวจบทที่ 2, ดูการอ้างอิง, ตรวจระเบียบวิธี...",
                key="review_focus_input",
                height=80,
            )
            if st.button("🎓 ตรวจงาน", type="primary",
                         key="advisor_review_btn", use_container_width=True):
                editor_text = st.session_state.get("work_content_input", "")
                if not editor_text or not editor_text.strip():
                    st.warning("⚠️ ไม่มีเนื้อหาใน Workbench ให้ตรวจ")
                else:
                    with st.spinner("🎓 อาจารย์กำลังตรวจงาน..."):
                        try:
                            # RAG retrieval: use editor title + user focus as query
                            _review_rag_query = (
                                (work_title.strip() + " " + review_focus.strip()).strip()
                                or editor_text[:200]
                            )
                            _review_retrieved = []
                            try:
                                _review_retrieved = _retrieve_reference_vault_docs(
                                    _review_rag_query, user_id, k=4,
                                )
                            except Exception:
                                _review_retrieved = []
                            _review_retrieved = _filter_reference_vault_docs(_review_retrieved)
                            st.session_state.review_retrieved_docs = _review_retrieved
                            review_text, ri, ro = review_research(
                                editor_text,
                                user_focus=review_focus,
                                retrieved_docs=_review_retrieved,
                            )
                            st.session_state.total_tokens += ri + ro
                            st.session_state.input_tokens += ri
                            st.session_state.output_tokens += ro
                            database.record_token_usage(user_id, ri, ro, "review_research")
                            st.session_state.review_result = review_text
                            st.session_state.review_expanded = True
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ เกิดข้อผิดพลาด: {str(e)}")

        # Reference Vault Citation Section
        with st.expander("Reference Vault citations", expanded=False):
            st.caption(
                "Citations must be rendered by the backend from SQLite Reference Vault metadata "
                "in [author_name, paper_name] format."
            )
            st.button(
                "Generate Reference Vault citations",
                type="primary",
                key="auto_citation_btn",
                use_container_width=True,
                disabled=True,
            )
            st.info(
                "Citation generation is disabled until the backend-verified citation renderer is available. "
                "Legacy citation output is not exposed as v3 citation output."
            )

        # ── Compare / Critically Analyze Papers Section ────────────────────────
        with st.expander("🔬 วิเคราะห์-เปรียบเทียบงานวิจัย ในแหล่งความรู้", expanded=False):
            _compare_vault_docs = [
                d for d in database.list_reference_vault_documents(user_id, status="active")
                if d.get("docling_status") == "succeeded"
            ]
            _compare_doc_options = {
                f"{d.get('paper_name') or d.get('filename') or 'Untitled paper'} ({d['document_id'][:8]})": d["document_id"]
                for d in _compare_vault_docs
            }
            _available_papers = list(_compare_doc_options.keys())
            if not _available_papers:
                st.info("ℹ️ ยังไม่มีเอกสารในแหล่งความรู้ กรุณาอัปโหลดเอกสารก่อน")
            else:
                _valid_defaults = [
                    p for p in st.session_state.compare_selected_papers
                    if p in _available_papers
                ][:3]
                _selected_papers = st.multiselect(
                    "เลือกงานวิจัยที่ต้องการวิเคราะห์ (สูงสุด 3 งานวิจัย)",
                    options=_available_papers,
                    default=_valid_defaults,
                    key="compare_papers_multiselect",
                    placeholder="เลือกอย่างน้อย 1 งานวิจัย...",
                )
                if len(_selected_papers) > 3:
                    st.warning("⚠️ เลือกได้สูงสุด 3 งานวิจัย — ระบบจะใช้เฉพาะ 3 รายการแรก")
                    _selected_papers = _selected_papers[:3]
                st.session_state.compare_selected_papers = _selected_papers

                if st.button(
                    "🔬 วิเคราะห์-เปรียบเทียบงานวิจัย ในแหล่งความรู้",
                    type="primary",
                    key="compare_papers_btn",
                    use_container_width=True,
                    disabled=len(_selected_papers) == 0,
                ):
                    try:
                        _QUERY_OBJECTIVES = (
                            "วัตถุประสงค์การวิจัย คำถามวิจัย บทคัดย่อ กรอบทฤษฎีแนวคิด"
                        )
                        _QUERY_METHODS = (
                            "ระเบียบวิธีวิจัย กลุ่มตัวอย่าง เครื่องมือวิจัย "
                            "ผลการวิจัย ข้อค้นพบ ข้อสรุป ข้อจำกัด"
                        )
                        with st.spinner("🔍 กำลังดึงข้อมูลงานวิจัยจากแหล่งความรู้..."):
                            _all_retrieved = []
                            _paper_sections = []
                            for _pname in _selected_papers:
                                _doc_id = _compare_doc_options.get(_pname)
                                _seen_fps: set = set()
                                _merged_docs = []
                                for _q in [_QUERY_OBJECTIVES, _QUERY_METHODS]:
                                    for _d in _retrieve_reference_vault_docs(
                                        _q,
                                        user_id,
                                        k=5,
                                        document_ids=[_doc_id] if _doc_id else None,
                                    ):
                                        _fp = _d.page_content[:80]
                                        if _fp not in _seen_fps:
                                            _seen_fps.add(_fp)
                                            _merged_docs.append(_d)
                                _all_retrieved.extend(_merged_docs)
                                _chunks_text = (
                                    "\n\n".join(
                                        d.page_content[:800] for d in _merged_docs
                                    )[:4800]
                                    if _merged_docs
                                    else "ไม่พบข้อมูลสำหรับงานนี้ในแหล่งความรู้"
                                )
                                _paper_sections.append(
                                    f"=== งานวิจัย: {_pname} ===\n{_chunks_text}"
                                )
                            _papers_context = "\n\n".join(_paper_sections)[:13000]
                            st.session_state.compare_retrieved_docs = _all_retrieved

                        st.markdown("### 🔬 กำลังวิเคราะห์...")
                        _streamed = st.write_stream(
                            analyze_papers_critically_stream(
                                _papers_context, _selected_papers
                            )
                        )
                        _approx_in = max(1, len(_papers_context) // 4)
                        _approx_out = max(1, len(str(_streamed)) // 4)
                        st.session_state.total_tokens += _approx_in + _approx_out
                        st.session_state.input_tokens += _approx_in
                        st.session_state.output_tokens += _approx_out
                        database.record_token_usage(
                            user_id, _approx_in, _approx_out, "compare_papers"
                        )
                        st.session_state.compare_result = str(_streamed)
                        st.session_state.compare_expanded = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ เกิดข้อผิดพลาด: {str(e)}")

        # ── Load panel ─────────────────────────────────────────────────────────
        if st.session_state.get("work_load_select"):
            work_docs = list_work_docs(user_id)
            if work_docs:
                display_names = [d["name"] for d in work_docs]
                selected_name = st.selectbox("Select a document to load",
                                             options=display_names,
                                             key="work_file_selectbox")
                if st.button("✅ Load into editor", key="confirm_load_btn"):
                    doc = next((d for d in work_docs if d["name"] == selected_name), None)
                    if doc:
                        st.session_state["_pending_work_title"] = doc["title"]
                        st.session_state["_pending_work_content"] = doc["content"]
                        st.session_state.work_current_file = doc["name"]
                        st.session_state.work_load_select = False
                        st.rerun()
            else:
                st.info("No saved work documents found.")

        # ── Save dialog ────────────────────────────────────────────────────────
        if st.session_state.get("work_save_dialog"):
            st.markdown("**💾 ตั้งชื่อไฟล์**")
            dialog_name = st.text_input(
                "ชื่อไฟล์",
                value=st.session_state.work_save_dialog_name,
                placeholder="ระบุชื่อไฟล์...",
                key="save_dialog_name_input"
            )
            col_confirm_s, col_cancel_s = st.columns([1, 1])
            with col_confirm_s:
                confirm_save = st.button("✅ บันทึก",
                                         key="confirm_save_dialog_btn",
                                         use_container_width=True)
            with col_cancel_s:
                cancel_save = st.button("❌ ยกเลิก",
                                        key="cancel_save_dialog_btn",
                                        use_container_width=True)
            if confirm_save:
                if dialog_name.strip():
                    if st.session_state.get("work_save_dialog") == "save_as":
                        doc_name = save_work_to_db_new(
                            user_id, dialog_name, work_title, work_content
                        )
                    else:
                        doc_name = save_work_to_db(
                            user_id, dialog_name, work_title, work_content
                        )
                    st.session_state.work_current_file = doc_name
                    st.session_state.work_save_dialog = None
                    st.success(f"✅ Saved → `{doc_name}`")
                    st.rerun()
                else:
                    st.warning("⚠️ กรุณาระบุชื่อไฟล์")
            if cancel_save:
                st.session_state.work_save_dialog = None
                st.rerun()

        # ── Import panel ───────────────────────────────────────────────────────
        if st.session_state.get("work_import_open"):
            import_file = st.file_uploader(
                "เลือกไฟล์ที่ต้องการ import",
                type=["txt", "docx", "doc"],
                key="import_file_uploader"
            )
            if import_file is not None:
                try:
                    ext = os.path.splitext(import_file.name)[1].lower()
                    if ext == ".txt":
                        imported_content = import_file.read().decode("utf-8")
                    elif ext in (".docx", ".doc"):
                        import docx2txt, io
                        imported_content = docx2txt.process(
                            io.BytesIO(import_file.read())
                        )
                    else:
                        imported_content = import_file.read().decode(
                            "utf-8", errors="replace"
                        )
                    st.session_state["_pending_work_title"] = (
                        os.path.splitext(import_file.name)[0]
                    )
                    st.session_state["_pending_work_content"] = imported_content
                    st.session_state.work_current_file = None
                    st.session_state.work_import_open = False
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ ไม่สามารถ import ไฟล์ได้: {str(e)}")
            if st.button("❌ ยกเลิก import", key="cancel_import_btn"):
                st.session_state.work_import_open = False
                st.rerun()

        # ── Export panel ───────────────────────────────────────────────────────
        if st.session_state.get("work_export_open"):
            export_data = (
                f"TITLE: {work_title}\n---\n{work_content}"
                if (work_title.strip() and work_content.strip()) else ""
            )
            default_fname = (
                re.sub(r'[\\/*?:"<>|]', "_", work_title.strip())[:60] or "work"
            ) + ".txt"
            export_fname_input = st.text_input(
                "📁 ชื่อไฟล์ที่ต้องการบันทึก",
                value=default_fname,
                key="export_fname_input"
            )
            col_dl, col_cancel_ex = st.columns([1, 1])
            with col_dl:
                st.download_button(
                    "📥 ดาวน์โหลด",
                    data=export_data.encode("utf-8") if export_data else b"",
                    file_name=export_fname_input or default_fname,
                    mime="text/plain",
                    key="export_download_btn",
                    use_container_width=True,
                    disabled=not export_data,
                )
            with col_cancel_ex:
                if st.button("❌ ยกเลิก", key="cancel_export_btn", use_container_width=True):
                    st.session_state.work_export_open = False
                    st.rerun()

        current_file = st.session_state.get("work_current_file")

        # ── Row 1: File actions ────────────────────────────────────────────
        c_save, c_saveas, c_load, c_export, c_import = st.columns(5)
        with c_save:
            save_clicked = st.button("💾 Save", type="primary",
                                     key="save_work_btn", use_container_width=True)
        with c_saveas:
            save_as_clicked = st.button("📑 Save As",
                                        key="save_as_work_btn", use_container_width=True)
        with c_load:
            load_work_clicked = st.button("📂 Load",
                                          key="load_work_btn", use_container_width=True)
        with c_export:
            export_clicked = st.button("📤 Export",
                                       key="export_work_btn",
                                       use_container_width=True)
        with c_import:
            import_clicked = st.button("📥 Import",
                                       key="import_work_btn", use_container_width=True)

        # ── Row 2: Edit actions ───────────────────────────────────────────
        c_undo, c_redo, c_clear = st.columns(3)
        with c_undo:
            undo_clicked = st.button(
                "↩️ Undo",
                key="ai_undo_btn",
                use_container_width=True,
                disabled=not st.session_state.ai_edit_undo_stack,
                help="Undo the last AI edit",
            )
        with c_redo:
            redo_clicked = st.button(
                "↪️ Redo",
                key="ai_redo_btn",
                use_container_width=True,
                disabled=not st.session_state.ai_edit_redo_stack,
                help="Redo the last undone AI edit",
            )
        with c_clear:
            clear_editor_clicked = st.button("🗑️ Clear",
                                             key="clear_editor_btn", use_container_width=True)

        # ── Button logic ───────────────────────────────────────────────────────
        _MAX_SAVED_DOCS = 20

        if save_clicked:
            if not work_title.strip() or not work_content.strip():
                st.warning("⚠️ Please enter both a title and content.")
            elif current_file:
                # Overwrite existing doc by name in SQLite (no count increase)
                save_work_to_db(user_id, current_file, work_title, work_content)
                st.success(f"✅ Saved → `{current_file}`")
            else:
                _existing_count = len(list_work_docs(user_id))
                if _existing_count >= _MAX_SAVED_DOCS:
                    st.error(
                        f"❌ ถึงขีดจำกัด {_MAX_SAVED_DOCS} ไฟล์ที่บันทึกไว้ "
                        "กรุณาลบเอกสารเก่าก่อนบันทึกไฟล์ใหม่"
                    )
                else:
                    st.session_state.work_save_dialog = "save"
                    st.session_state.work_save_dialog_name = work_title
                    st.session_state.work_load_select = False
                    st.session_state.work_import_open = False
                    st.session_state.work_export_open = False
                    st.rerun()

        if save_as_clicked:
            if not work_title.strip() or not work_content.strip():
                st.warning("⚠️ Please enter both a title and content.")
            else:
                _existing_count = len(list_work_docs(user_id))
                if _existing_count >= _MAX_SAVED_DOCS:
                    st.error(
                        f"❌ ถึงขีดจำกัด {_MAX_SAVED_DOCS} ไฟล์ที่บันทึกไว้ "
                        "กรุณาลบเอกสารเก่าก่อนบันทึกไฟล์ใหม่"
                    )
                else:
                    st.session_state.work_save_dialog = "save_as"
                    st.session_state.work_save_dialog_name = work_title
                    st.session_state.work_load_select = False
                    st.session_state.work_import_open = False
                    st.session_state.work_export_open = False
                    st.rerun()

        if load_work_clicked:
            st.session_state.work_load_select = True
            st.session_state.work_save_dialog = None
            st.session_state.work_import_open = False
            st.session_state.work_export_open = False
            st.rerun()

        if import_clicked:
            st.session_state.work_import_open = True
            st.session_state.work_load_select = False
            st.session_state.work_save_dialog = None
            st.session_state.work_export_open = False
            st.rerun()

        if export_clicked:
            st.session_state.work_export_open = True
            st.session_state.work_load_select = False
            st.session_state.work_save_dialog = None
            st.session_state.work_import_open = False
            st.rerun()

        if clear_editor_clicked:
            st.session_state["_pending_work_title"] = ""
            st.session_state["_pending_work_content"] = ""
            st.session_state.work_title_val = ""
            st.session_state.work_content_val = ""
            st.session_state.work_current_file = None
            st.session_state.ai_edit_undo_stack = []
            st.session_state.ai_edit_redo_stack = []
            st.rerun()

        if undo_clicked and st.session_state.ai_edit_undo_stack:
            st.session_state.ai_edit_redo_stack.append(work_content)
            restored = st.session_state.ai_edit_undo_stack.pop()
            st.session_state["_pending_work_content"] = restored
            st.session_state.work_content_val = restored
            st.rerun()

        if redo_clicked and st.session_state.ai_edit_redo_stack:
            st.session_state.ai_edit_undo_stack.append(work_content)
            restored = st.session_state.ai_edit_redo_stack.pop()
            st.session_state["_pending_work_content"] = restored
            st.session_state.work_content_val = restored
            st.rerun()

        st.divider()

        # ── Research Guide Result (below editor) ─────────────────────────────
        if st.session_state.get("research_guide_result"):
            _guide_think, _guide_body = parse_think_content(
                st.session_state.research_guide_result
            )
            with st.expander("🧭 ผลแนวทางการวิจัย", expanded=True):
                if _guide_think:
                    with st.expander("💭 ความคิด (Thinking)", expanded=False):
                        st.markdown(
                            f'<div class="think-block">{_guide_think}</div>',
                            unsafe_allow_html=True,
                        )
                st.markdown(_guide_body)
                # Reference docs
                _guide_ref_docs = st.session_state.get("research_guide_retrieved_docs")
                if _guide_ref_docs:
                    _guide_ref_label = f"📚 เอกสารอ้างอิงที่ใช้ — {len(_guide_ref_docs)} รายการ"
                    with st.expander(_guide_ref_label, expanded=False):
                        for i, doc in enumerate(_guide_ref_docs, 1):
                            st.markdown(f"**📄 Doc {i}:** {doc.metadata.get('paper_title', doc.metadata.get('doc_name', ''))}")
                            preview = doc.page_content[:300]
                            st.text(preview + "..." if len(doc.page_content) > 300 else preview)

                # Action buttons
                _gb1, _gb2, _gb3 = st.columns(3)
                with _gb1:
                    if st.button("📥 แทนที่เนื้อหาในตัวแก้ไข", key="guide_replace_btn",
                                 use_container_width=True):
                        st.session_state.ai_edit_undo_stack.append(work_content)
                        if len(st.session_state.ai_edit_undo_stack) > 20:
                            st.session_state.ai_edit_undo_stack.pop(0)
                        st.session_state.ai_edit_redo_stack.clear()
                        st.session_state["_pending_work_content"] = _guide_body
                        st.session_state.work_content_val = _guide_body
                        st.session_state.research_guide_result = None
                        st.session_state.research_guide_retrieved_docs = None
                        st.rerun()
                with _gb2:
                    if st.button("➕ เพิ่มเนื้อหาต่อท้าย", key="guide_append_btn",
                                 use_container_width=True):
                        _cur = st.session_state.get("work_content_val", "")
                        st.session_state.ai_edit_undo_stack.append(_cur)
                        if len(st.session_state.ai_edit_undo_stack) > 20:
                            st.session_state.ai_edit_undo_stack.pop(0)
                        st.session_state.ai_edit_redo_stack.clear()
                        _new = _cur + "\n\n" + _guide_body
                        st.session_state["_pending_work_content"] = _new
                        st.session_state.work_content_val = _new
                        st.session_state.research_guide_result = None
                        st.session_state.research_guide_retrieved_docs = None
                        st.rerun()
                with _gb3:
                    if st.button("❌ ยกเลิก", key="guide_cancel_btn",
                                 use_container_width=True):
                        st.session_state.research_guide_result = None
                        st.session_state.research_guide_retrieved_docs = None
                        st.rerun()

        # ── Advisor Review Result (below editor) ─────────────────────────────
        if st.session_state.review_result:
            with st.expander("🎓 ผลการตรวจจากอาจารย์ที่ปรึกษา",
                             expanded=st.session_state.review_expanded):
                _review_think, _review_body = parse_think_content(st.session_state.review_result)
                if _review_think:
                    with st.expander("💭 ความคิด (Thinking)", expanded=False):
                        st.markdown(
                            f'<div class="think-block">{_review_think}</div>',
                            unsafe_allow_html=True,
                        )
                _render_review_result(_review_body)
                _rev_docs = _filter_reference_vault_docs(st.session_state.get("review_retrieved_docs"))
                _rev_label = f"Reference Vault sources - {len(_rev_docs)} paper(s)" if _rev_docs else "Reference Vault sources - insufficient evidence"
                with st.expander(_rev_label, expanded=False):
                    _render_reference_vault_sources(_rev_docs)
                if st.button("🗑️ ล้างผลตรวจ", key="clear_review_btn",
                             use_container_width=True):
                    st.session_state.review_result = None
                    st.session_state.review_retrieved_docs = None
                    st.rerun()

        # Reference Vault Citation Result
        if st.session_state.citation_result:
            with st.expander("Reference Vault citation result", expanded=True):
                st.warning(
                    "Existing citation output was generated by the legacy citation path and is not shown as v3 output. "
                    "Regenerate after the backend-verified [author_name, paper_name] renderer is available."
                )
                if st.button("Clear citation result", key="reject_citation_btn", use_container_width=True):
                    st.session_state.citation_result = None
                    st.session_state.citation_cited_content = None
                    st.session_state.citation_sources = []
                    st.rerun()

        if st.session_state.compare_result:
            with st.expander(
                "Compare papers result",
                expanded=st.session_state.compare_expanded,
            ):
                _cmp_think, _cmp_body = parse_think_content(
                    st.session_state.compare_result
                )
                if _cmp_think:
                    with st.expander("Thinking", expanded=False):
                        st.markdown(
                            f'<div class="think-block">{_cmp_think}</div>',
                            unsafe_allow_html=True,
                        )
                st.markdown(_cmp_body)
                _cmp_docs = _filter_reference_vault_docs(
                    st.session_state.get("compare_retrieved_docs")
                )
                _cmp_label = (
                    f"Reference Vault sources - {len(_cmp_docs)} paper(s)"
                    if _cmp_docs
                    else "Reference Vault sources - insufficient evidence"
                )
                with st.expander(_cmp_label, expanded=False):
                    _render_reference_vault_sources(_cmp_docs)
                if st.button(
                    "Clear compare result",
                    key="clear_compare_btn",
                    use_container_width=True,
                ):
                    st.session_state.compare_result = None
                    st.session_state.compare_retrieved_docs = None
                    st.rerun()

        st.divider()
        with st.expander("Session usage", expanded=False):
            _in_tok = st.session_state.get("input_tokens", 0)
            _out_tok = st.session_state.get("output_tokens", 0)
            _total_tok = _in_tok + _out_tok
            st.markdown(
                f"**Input:** {_in_tok:,} tokens &nbsp;|&nbsp; "
                f"**Output:** {_out_tok:,} tokens &nbsp;|&nbsp; "
                f"**Total:** {_total_tok:,}",
                unsafe_allow_html=True,
            )

    # Right: Chat Assistant
    with col_right:
        st.markdown(
            "<div style='font-size:1.35rem;font-weight:700;color:#1f2937;padding:0.25rem 0 0.4rem 0;'>Chat Assistant</div>",
            unsafe_allow_html=True,
        )

        if st.session_state.messages:
            chat_container = st.container(height=300)
            with chat_container:
                for message in st.session_state.messages:
                    with st.chat_message(message["role"]):
                        if message["role"] == "assistant":
                            if message.get("action") == "research":
                                st.caption("Research result is in the Workbench")
                            elif message.get("action") == "edit":
                                st.caption("Workbench edited")
                            display_assistant_message(message["content"])
                            if "tokens" in message:
                                st.caption(f"{message['tokens']:,} tokens (turn)")
                            if "sources" in message:
                                _msg_docs = _filter_reference_vault_docs(message["sources"])
                                _msg_label = (
                                    f"Reference Vault sources - {len(_msg_docs)} paper(s)"
                                    if _msg_docs
                                    else "Reference Vault sources - insufficient evidence"
                                )
                                with st.expander(_msg_label, expanded=False):
                                    _render_reference_vault_sources(_msg_docs)
                        else:
                            st.write(message["content"])

        # Placeholder for spinner
        _chat_spinner_area = st.empty()

        # ── JS helpers: widget warnings + content edit overlay ─────────────
        import streamlit.components.v1 as components
        components.html("""
        <script>
        // ── Hide Streamlit widget default-value warnings ──
        const _hideWidgetWarnings = () => {
            const alerts = parent.document.querySelectorAll('[data-testid="stAlert"], .stAlert');
            alerts.forEach(el => {
                if (el.textContent.includes('was created with a default value')) {
                    el.style.display = 'none';
                }
            });
        };
        setInterval(_hideWidgetWarnings, 300);

        // ── Content Edit: right-click on selected text → floating chatbox ──
        const _removeEditOverlay = () => {
            const el = parent.document.getElementById('__ceOverlay');
            if (el) el.remove();
        };

        const _showEditOverlay = (x, y, selectedText) => {
            _removeEditOverlay();
            const overlay = parent.document.createElement('div');
            overlay.id = '__ceOverlay';
            overlay.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:999999;background:rgba(0,0,0,0.12);';

            const boxW = 350;
            let bx = Math.min(x + 4, parent.innerWidth - boxW - 16);
            let by = Math.min(y + 4, parent.innerHeight - 260);
            bx = Math.max(8, bx); by = Math.max(8, by);

            const preview = selectedText.length > 120
                ? selectedText.substring(0, 120).replace(/</g, '&lt;') + '...'
                : selectedText.replace(/</g, '&lt;');

            overlay.innerHTML = `
            <div style="position:fixed;left:${bx}px;top:${by}px;width:${boxW}px;background:#fff;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,0.2);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;overflow:hidden;border:1px solid #e0e0e0;">
                <div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;padding:10px 16px;font-weight:600;font-size:14px;display:flex;justify-content:space-between;align-items:center;">
                    <span>&#9999;&#65039; Edit</span>
                    <span id="__ceClose" style="cursor:pointer;font-size:18px;opacity:0.8;">&#10005;</span>
                </div>
                <div style="padding:12px 16px;">
                    <div style="background:#f8f9fa;border-radius:6px;padding:8px 10px;margin-bottom:10px;font-size:12px;color:#555;max-height:60px;overflow-y:auto;border:1px solid #eee;white-space:pre-wrap;word-break:break-word;">${preview}</div>
                    <input type="text" id="__ceInput" placeholder="อธิบายว่าต้องการแก้ไขอย่างไร..."
                        style="width:100%;box-sizing:border-box;padding:9px 12px;border:1.5px solid #ddd;border-radius:8px;font-size:13px;outline:none;"
                    />
                    <div style="display:flex;gap:8px;margin-top:10px;">
                        <button id="__ceSubmit" style="flex:1;padding:9px;border:none;border-radius:8px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);color:#fff;font-weight:600;cursor:pointer;font-size:13px;">แก้ไข</button>
                        <button id="__ceCancel" style="flex:1;padding:9px;border:1.5px solid #ddd;border-radius:8px;background:#fff;color:#555;cursor:pointer;font-size:13px;">ยกเลิก</button>
                    </div>
                </div>
            </div>`;

            parent.document.body.appendChild(overlay);
            setTimeout(() => {
                const inp = parent.document.getElementById('__ceInput');
                if (inp) inp.focus();
            }, 50);

            // Submit handler
            parent.document.getElementById('__ceSubmit').addEventListener('click', () => {
                const instruction = parent.document.getElementById('__ceInput').value.trim();
                if (!instruction) return;
                const cmd = '__EDIT__' + JSON.stringify({s: selectedText, i: instruction});
                const chatTA = parent.document.querySelector('textarea[data-testid="stChatInputTextArea"]');
                if (chatTA) {
                    const nset = Object.getOwnPropertyDescriptor(
                        window.HTMLTextAreaElement.prototype, 'value'
                    ).set;
                    nset.call(chatTA, cmd);
                    chatTA.dispatchEvent(new Event('input', {bubbles: true}));
                    setTimeout(() => {
                        const btn = parent.document.querySelector('button[data-testid="stChatInputSubmitButton"]');
                        if (btn) btn.click();
                    }, 150);
                }
                _removeEditOverlay();
            });

            // Enter key submits
            parent.document.getElementById('__ceInput').addEventListener('keydown', (e) => {
                if (e.key === 'Enter') parent.document.getElementById('__ceSubmit').click();
            });

            // Cancel / close
            parent.document.getElementById('__ceCancel').addEventListener('click', _removeEditOverlay);
            parent.document.getElementById('__ceClose').addEventListener('click', _removeEditOverlay);
            overlay.addEventListener('click', (e) => { if (e.target === overlay) _removeEditOverlay(); });
        };

        // ── Content Insert: right-click without selection → floating insert box ──
        const _removeInsertOverlay = () => {
            const el = parent.document.getElementById('__ciOverlay');
            if (el) el.remove();
        };

        const _showInsertOverlay = (x, y, cursorPos) => {
            _removeInsertOverlay();
            const overlay = parent.document.createElement('div');
            overlay.id = '__ciOverlay';
            overlay.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:999999;background:rgba(0,0,0,0.12);';

            const boxW = 350;
            let bx = Math.min(x + 4, parent.innerWidth - boxW - 16);
            let by = Math.min(y + 4, parent.innerHeight - 220);
            bx = Math.max(8, bx); by = Math.max(8, by);

            overlay.innerHTML = `
            <div style="position:fixed;left:${bx}px;top:${by}px;width:${boxW}px;background:#fff;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,0.2);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;overflow:hidden;border:1px solid #e0e0e0;">
                <div style="background:linear-gradient(135deg,#43e97b 0%,#38f9d7 100%);color:#fff;padding:10px 16px;font-weight:600;font-size:14px;display:flex;justify-content:space-between;align-items:center;">
                    <span>&#10133; Insert</span>
                    <span id="__ciClose" style="cursor:pointer;font-size:18px;opacity:0.8;">&#10005;</span>
                </div>
                <div style="padding:12px 16px;">
                    <div style="font-size:12px;color:#888;margin-bottom:8px;">แทรกข้อความที่ตำแหน่ง cursor</div>
                    <input type="text" id="__ciInput" placeholder="อธิบายว่าต้องการแทรกข้อความอะไร..."
                        style="width:100%;box-sizing:border-box;padding:9px 12px;border:1.5px solid #ddd;border-radius:8px;font-size:13px;outline:none;"
                    />
                    <div style="display:flex;gap:8px;margin-top:10px;">
                        <button id="__ciSubmit" style="flex:1;padding:9px;border:none;border-radius:8px;background:linear-gradient(135deg,#43e97b 0%,#38f9d7 100%);color:#fff;font-weight:600;cursor:pointer;font-size:13px;">แทรก</button>
                        <button id="__ciCancel" style="flex:1;padding:9px;border:1.5px solid #ddd;border-radius:8px;background:#fff;color:#555;cursor:pointer;font-size:13px;">ยกเลิก</button>
                    </div>
                </div>
            </div>`;

            parent.document.body.appendChild(overlay);
            setTimeout(() => {
                const inp = parent.document.getElementById('__ciInput');
                if (inp) inp.focus();
            }, 50);

            // Submit handler
            parent.document.getElementById('__ciSubmit').addEventListener('click', () => {
                const instruction = parent.document.getElementById('__ciInput').value.trim();
                if (!instruction) return;
                const cmd = '__INSERT__' + JSON.stringify({pos: cursorPos, i: instruction});
                const chatTA = parent.document.querySelector('textarea[data-testid="stChatInputTextArea"]');
                if (chatTA) {
                    const nset = Object.getOwnPropertyDescriptor(
                        window.HTMLTextAreaElement.prototype, 'value'
                    ).set;
                    nset.call(chatTA, cmd);
                    chatTA.dispatchEvent(new Event('input', {bubbles: true}));
                    setTimeout(() => {
                        const btn = parent.document.querySelector('button[data-testid="stChatInputSubmitButton"]');
                        if (btn) btn.click();
                    }, 150);
                }
                _removeInsertOverlay();
            });

            // Enter key submits
            parent.document.getElementById('__ciInput').addEventListener('keydown', (e) => {
                if (e.key === 'Enter') parent.document.getElementById('__ciSubmit').click();
            });

            // Cancel / close
            parent.document.getElementById('__ciCancel').addEventListener('click', _removeInsertOverlay);
            parent.document.getElementById('__ciClose').addEventListener('click', _removeInsertOverlay);
            overlay.addEventListener('click', (e) => { if (e.target === overlay) _removeInsertOverlay(); });
        };

        // ── Event Delegation ──
        if (parent.window._customContextMenuListener) {
            parent.document.removeEventListener('contextmenu', parent.window._customContextMenuListener);
        }

        parent.window._customContextMenuListener = function(e) {
            const ta = e.target;
            if (ta && ta.tagName === 'TEXTAREA' && ta.placeholder && ta.placeholder.includes('Start writing')) {
                const sel = ta.value.substring(ta.selectionStart, ta.selectionEnd);
                if (sel.trim().length > 0) {
                    e.preventDefault();
                    _showEditOverlay(e.clientX, e.clientY, sel);
                } else {
                    e.preventDefault();
                    _showInsertOverlay(e.clientX, e.clientY, ta.selectionStart);
                }
            }
        };

        parent.document.addEventListener('contextmenu', parent.window._customContextMenuListener);
        </script>
        """, height=0)

        # ── Highlight edited text after rerun ─────────────────────────────
        _hl = st.session_state.pop("_highlight_sel", None)
        if _hl:
            _hl_start, _hl_end = _hl
            components.html(f"""
            <script>
            (function() {{
                const _highlightEdited = () => {{
                    const textareas = parent.document.querySelectorAll('textarea');
                    for (const ta of textareas) {{
                        if (ta.placeholder && ta.placeholder.includes('Start writing')) {{
                            ta.focus();
                            ta.setSelectionRange({_hl_start}, {_hl_end});
                            const lineHeight = parseInt(getComputedStyle(ta).lineHeight) || 20;
                            const approxLine = ta.value.substring(0, {_hl_start}).split('\\n').length;
                            ta.scrollTop = Math.max(0, (approxLine - 3) * lineHeight);
                            return true;
                        }}
                    }}
                    return false;
                }};
                let tries = 0;
                const iv = setInterval(() => {{
                    if (_highlightEdited() || ++tries > 10) clearInterval(iv);
                }}, 200);
            }})();
            </script>
            """, height=0)

        _input_placeholder = (
            "📖 ถามคำถาม — AI จะตอบเชิงลึก..."
            if st.session_state._research_mode
            else "ถามคำถาม..."
        )
        prompt = st.chat_input(_input_placeholder, key="chat_input_main")

        _deep_mode = st.toggle(
            "Deep answer",
            value=st.session_state._research_mode,
            key="_deep_toggle",
            help="On: detailed research-style answer. Off: concise answer.",
        )
        st.session_state._research_mode = _deep_mode

        if st.button(
            "Clear chat history",
            key="clear_chat_btn",
            type="secondary",
            use_container_width=True,
        ):
            st.session_state.messages = []
            st.session_state.total_tokens = 0
            st.session_state.input_tokens = 0
            st.session_state.output_tokens = 0
            st.session_state.total_cost_thb = 0.0
            st.rerun()

        # ── Detect content edit command from right-click overlay ──────
        if prompt:
            if prompt.startswith("__EDIT__"):
                try:
                    edit_data = json.loads(prompt[8:])
                    selected = edit_data["s"]
                    instruction = edit_data["i"]

                    with _chat_spinner_area, st.spinner("✏️ กำลังแก้ไขข้อความที่เลือก..."):
                        # RAG retrieval: use instruction + selected snippet as query
                        _sel_rag_query = f"{instruction} {selected[:150]}"
                        _sel_retrieved = []
                        try:
                            _sel_retrieved = _retrieve_reference_vault_docs(
                                _sel_rag_query, user_id, k=3,
                            )
                        except Exception:
                            _sel_retrieved = []
                        _sel_retrieved = _filter_reference_vault_docs(_sel_retrieved)
                        sel_think, edited, ri, ro = generate_selection_edit(
                            selected, instruction,
                            retrieved_docs=_sel_retrieved,
                        )
                        new_content = work_content.replace(selected, edited, 1)

                        st.session_state.ai_edit_undo_stack.append(work_content)
                        if len(st.session_state.ai_edit_undo_stack) > 20:
                            st.session_state.ai_edit_undo_stack.pop(0)
                        st.session_state.ai_edit_redo_stack = []
                        st.session_state["_pending_work_content"] = new_content
                        st.session_state.work_content_val = new_content

                        edit_start = new_content.find(edited)
                        if edit_start >= 0:
                            st.session_state["_highlight_sel"] = (edit_start, edit_start + len(edited))

                        st.session_state.total_tokens += ri + ro
                        st.session_state.input_tokens += ri
                        st.session_state.output_tokens += ro
                        database.record_token_usage(user_id, ri, ro, "generate_selection_edit")

                        _edit_msg = (
                            f"✏️ แก้ไขข้อความที่เลือกเรียบร้อยแล้ว\n\n"
                            f"คำสั่ง: {instruction}"
                        )
                        if sel_think:
                            _edit_msg = f"<think>{sel_think}</think>\n\n{_edit_msg}"
                        st.session_state.messages.append({
                            "role": "user",
                            "content": f"✏️ แก้ไขข้อความ: {instruction}",
                        })
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": _edit_msg,
                            "tokens": ri + ro,
                            "action": "edit",
                            "sources": _sel_retrieved,
                        })
                    st.rerun()
                except Exception as e:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": f"❌ เกิดข้อผิดพลาดในการแก้ไข: {str(e)}"
                    })
                    st.rerun()

            # ── Detect content insert command from right-click overlay ─────
            elif prompt.startswith("__INSERT__"):
                try:
                    insert_data = json.loads(prompt[10:])
                    cursor_pos = insert_data["pos"]
                    instruction = insert_data["i"]

                    with _chat_spinner_area, st.spinner("➕ กำลังสร้างข้อความแทรก..."):
                        context_before = work_content[:cursor_pos]
                        context_after = work_content[cursor_pos:]
                        # RAG retrieval: use instruction + surrounding text as query
                        _ins_surrounding = (
                            context_before[-100:] + " " + context_after[:100]
                        ).strip()
                        _ins_rag_query = f"{instruction} {_ins_surrounding}"
                        _ins_retrieved = []
                        try:
                            _ins_retrieved = _retrieve_reference_vault_docs(
                                _ins_rag_query, user_id, k=3,
                            )
                        except Exception:
                            _ins_retrieved = []
                        _ins_retrieved = _filter_reference_vault_docs(_ins_retrieved)
                        ins_think, inserted, ri, ro = generate_insertion(
                            context_before, context_after, instruction,
                            retrieved_docs=_ins_retrieved,
                        )
                        new_content = context_before + inserted + context_after

                        st.session_state.ai_edit_undo_stack.append(work_content)
                        if len(st.session_state.ai_edit_undo_stack) > 20:
                            st.session_state.ai_edit_undo_stack.pop(0)
                        st.session_state.ai_edit_redo_stack = []
                        st.session_state["_pending_work_content"] = new_content
                        st.session_state.work_content_val = new_content

                        st.session_state["_highlight_sel"] = (cursor_pos, cursor_pos + len(inserted))

                        st.session_state.total_tokens += ri + ro
                        st.session_state.input_tokens += ri
                        st.session_state.output_tokens += ro
                        database.record_token_usage(user_id, ri, ro, "generate_insertion")

                        _ins_msg = (
                            f"➕ แทรกข้อความเรียบร้อยแล้ว\n\n"
                            f"คำสั่ง: {instruction}"
                        )
                        if ins_think:
                            _ins_msg = f"<think>{ins_think}</think>\n\n{_ins_msg}"
                        st.session_state.messages.append({
                            "role": "user",
                            "content": f"➕ แทรกข้อความ: {instruction}",
                        })
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": _ins_msg,
                            "tokens": ri + ro,
                            "action": "edit",
                            "sources": _ins_retrieved,
                        })
                    st.rerun()
                except Exception as e:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": f"❌ เกิดข้อผิดพลาดในการแทรก: {str(e)}"
                    })
                    st.rerun()

            # ── Normal chat / research mode ────────────────────────────────
            else:
                is_research = st.session_state._research_mode
                actual_query = prompt
                if re.match(r'^/research\b\s*', prompt, re.IGNORECASE):
                    is_research = True
                    actual_query = re.sub(r'^/research\s*', '', prompt, flags=re.IGNORECASE).strip()
                    st.session_state._research_mode = True

                if is_research and not actual_query:
                    st.session_state._research_mode = True
                    st.rerun()

                st.session_state.messages.append({
                    "role": "user",
                    "content": actual_query,
                    "research": is_research,
                })

                try:
                    chat_history = st.session_state.messages[:-1]

                    # ── Query routing: skip vector DB for small talk ──────────
                    # is_small_talk() detects greetings / meta-questions so we
                    # avoid an unnecessary Pinecone round-trip entirely.
                    _is_small_talk = not is_research and is_small_talk(actual_query)
                    if _is_small_talk:
                        retrieved_docs = []
                    else:
                        retrieval_k = 5 if is_research else 3
                        retrieved_docs = _retrieve_reference_vault_docs(
                            actual_query, user_id, k=retrieval_k,
                        )

                    # ── Response generation ───────────────────────────────────
                    if is_research:
                        if not retrieved_docs:
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": INSUFFICIENT_VAULT_FALLBACK,
                                "sources": [],
                                "action": "fallback",
                            })
                            st.rerun()
                        # Research mode: must receive structured JSON → use
                        # blocking call then display result after full response
                        spinner_text = "🔬 กำลังค้นคว้าเชิงลึก..."
                        with _chat_spinner_area, st.spinner(spinner_text):
                            action, response_text, new_editor_content, input_tokens, output_tokens = (
                                generate_answer(
                                    actual_query, retrieved_docs, chat_history,
                                    editor_content=work_content,
                                    research_mode=True,
                                )
                            )

                        st.session_state.total_tokens += input_tokens + output_tokens
                        st.session_state.input_tokens += input_tokens
                        st.session_state.output_tokens += output_tokens
                        database.record_token_usage(user_id, input_tokens, output_tokens, "research_mode")

                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": response_text,
                            "sources": retrieved_docs,
                            "tokens": input_tokens + output_tokens,
                            "action": action,
                        })

                        if action in ("edit", "research") and new_editor_content:
                            st.session_state.ai_edit_undo_stack.append(work_content)
                            if len(st.session_state.ai_edit_undo_stack) > 20:
                                st.session_state.ai_edit_undo_stack.pop(0)
                            st.session_state.ai_edit_redo_stack = []
                            st.session_state["_pending_work_content"] = new_editor_content
                            st.session_state.work_content_val = new_editor_content

                    else:
                        # ── Chat mode with edit intent detection ──────────
                        # Lightweight local check: if user wants to edit the
                        # editor, use non-streaming path (needs JSON parsing).
                        _wants_edit = is_edit_intent(actual_query)

                        if not _is_small_talk and not _wants_edit and not retrieved_docs:
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": INSUFFICIENT_VAULT_FALLBACK,
                                "sources": [],
                                "action": "fallback",
                            })
                            st.rerun()

                        if _wants_edit:
                            # Edit-capable chat: non-streaming, may return
                            # action="edit" with new editor content
                            spinner_text = "กำลังแก้ไขเอกสาร..."
                            with _chat_spinner_area, st.spinner(spinner_text):
                                action, response_text, new_editor_content, input_tokens, output_tokens = (
                                    generate_answer(
                                        actual_query, retrieved_docs, chat_history,
                                        editor_content=work_content,
                                        research_mode=False,
                                        edit_capable=True,
                                    )
                                )

                            st.session_state.total_tokens += input_tokens + output_tokens
                            st.session_state.input_tokens += input_tokens
                            st.session_state.output_tokens += output_tokens
                            database.record_token_usage(user_id, input_tokens, output_tokens, "chat_edit")

                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": response_text,
                                "sources": retrieved_docs,
                                "tokens": input_tokens + output_tokens,
                                "action": action,
                            })

                            if action == "edit" and new_editor_content:
                                st.session_state.ai_edit_undo_stack.append(work_content)
                                if len(st.session_state.ai_edit_undo_stack) > 20:
                                    st.session_state.ai_edit_undo_stack.pop(0)
                                st.session_state.ai_edit_redo_stack = []
                                st.session_state["_pending_work_content"] = new_editor_content
                                st.session_state.work_content_val = new_editor_content

                        else:
                            # Plain chat mode streams OpenThaiGPT tokens into
                            # the chat panel. Edit/research paths stay
                            # non-streaming because they need structured JSON.
                            with st.chat_message("assistant"):
                                streamed_text = st.write_stream(
                                    generate_answer_stream(
                                        actual_query, retrieved_docs, chat_history,
                                        editor_content=work_content,
                                    )
                                )

                            input_tokens = max(
                                1,
                                (
                                    len(actual_query)
                                    + sum(len(getattr(doc, "page_content", "")) for doc in retrieved_docs)
                                    + len(work_content[-800:])
                                ) // 4,
                            )
                            output_tokens = max(1, len(str(streamed_text)) // 4)
                            st.session_state.total_tokens += input_tokens + output_tokens
                            st.session_state.input_tokens += input_tokens
                            st.session_state.output_tokens += output_tokens
                            database.record_token_usage(
                                user_id, input_tokens, output_tokens, "chat",
                            )
                            st.session_state.messages.append({
                                "role": "assistant",
                                "content": str(streamed_text),
                                "sources": retrieved_docs,
                                "tokens": input_tokens + output_tokens,
                                "action": "chat",
                            })

                except ValueError as e:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": str(e),
                    })
                except requests.exceptions.Timeout:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": "❌ การเชื่อมต่อ API หมดเวลา กรุณาลองใหม่อีกครั้ง",
                    })
                except requests.exceptions.ConnectionError:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": "❌ ไม่สามารถเชื่อมต่อ API ได้ กรุณาตรวจสอบการเชื่อมต่ออินเทอร์เน็ต",
                    })
                except Exception as e:
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": f"❌ เกิดข้อผิดพลาด: {str(e)}",
                    })
                st.rerun()


if __name__ == "__main__":
    main()
