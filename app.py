"""ChatGPT/Gemini 스타일 채팅 웹앱 (Streamlit).

실행:  streamlit run app.py
"""

from __future__ import annotations
import io
import json as _json
import re as _re
import base64 as _base64
import streamlit as st
import streamlit.components.v1 as _components

# ── 은행 홈페이지 URL 매핑 ────────────────────────────────────────────
_BANK_URLS: dict[str, str] = {
    "KB국민은행":  "https://obank.kbstar.com/quics?page=C016528",
    "국민은행":    "https://obank.kbstar.com/quics?page=C016528",
    "우리은행":    "https://spot.wooribank.com/pot/Dream?withyou=PODEP0001",
    "NH농협은행":  "https://wmall.nonghyup.com/servlet/SFBCW0000R.view",
    "농협은행":    "https://wmall.nonghyup.com/servlet/SFBCW0000R.view",
    "토스뱅크":   "https://www.tossbank.com/product-service/savings",
    "제주은행":    "https://www.jejubank.co.kr/hmpg/prdGdnc/sid.do",
    "SC제일은행":  "https://www.standardchartered.co.kr/np/kr/pl/se/SavingList.jsp",
    "신한은행":    "https://www.shinhan.com",
    "하나은행":    "https://www.kebhana.com",
    "카카오뱅크":  "https://www.kakaobank.com",
    "IBK기업은행": "https://www.ibk.co.kr",
    "기업은행":    "https://www.ibk.co.kr",
    "케이뱅크":   "https://www.kbanknow.com",
    "부산은행":    "https://www.busanbank.co.kr",
    "대구은행":    "https://www.dgb.co.kr",
    "전북은행":    "https://www.jbbank.co.kr",
    "경남은행":    "https://www.knbank.co.kr",
    "광주은행":    "https://www.kjbank.com",
}


def _inject_bank_links(text: str) -> str:
    """어시스턴트 응답에서 은행명을 찾아 홈페이지 ↗ 링크를 주입한다."""
    result = _re.sub(r'\n[ \t]*[-*○◦•]?\s*\[상품\s*페이지\]\([^)]*\)', '', text)
    links: list[str] = []

    def _stash(m: _re.Match) -> str:
        links.append(m.group(0))
        return f"\x00{len(links)-1}\x00"

    result = _re.sub(r'\[.*?\]\(.*?\)', _stash, result, flags=_re.DOTALL)
    for bank, url in sorted(_BANK_URLS.items(), key=lambda x: -len(x[0])):
        result = _re.sub(
            rf'(?<!\w){_re.escape(bank)}(?!\w)',
            lambda m, u=url: f'[{m.group(0)} ↗]({u})',
            result, count=1,
        )
    for i, original in enumerate(links):
        result = result.replace(f"\x00{i}\x00", original)
    return result


import requests

import agent
import cache
import config
import fss_fetcher
import llm
import rag
import search
import storage
import tracing

st.set_page_config(page_title="AI 채팅", page_icon="💬", layout="wide")

# ── CSS ─────────────────────────────────────────────────────────────
st.markdown("""<style>
[data-testid="stSidebarNav"] { display: none; }
#MainMenu, footer { visibility: hidden; }
.main .block-container {
    max-width: 780px;
    padding-top: 1.5rem;
    padding-bottom: 5rem;
    margin-left: auto;
    margin-right: auto;
}
[data-testid="stSidebar"] {
    background-color: #F8FAFD;
    border-right: 1px solid #DDE3EA;
}
/* 사이드바 상단 헤더(숨기기 버튼 행) 왼쪽에 'AI챗봇' 제목 삽입 */
[data-testid="stSidebarHeader"] {
    display: flex !important;
    align-items: center !important;
}
[data-testid="stSidebarHeader"]::before {
    content: "AI챗봇";
    font-size: 18px;
    font-weight: 700;
    color: #1A73E8;
    padding-left: 4px;
    white-space: nowrap;
}
/* 사이드바 상단 여백 축소 → 대화 목록 공간 확보 */
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
    padding-top: 0.75rem !important;
}
/* 이전 대화 버튼: 한 줄 말줄임 + 컴팩트하게 (더 많이 보이도록) */
[data-testid="stSidebar"] [data-testid="stButton"] > button {
    min-height: 0 !important;
    padding-top: 0.35rem !important;
    padding-bottom: 0.35rem !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] > button p {
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}
/* 대화 목록(제목·삭제): 배경·테두리 제거. columns 내부(stHorizontalBlock)만 타겟 */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stButton"] > button {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}
/* 현재 대화(primary): 배경 대신 파란 굵은 글씨로 구분 (흰 글씨 방지) */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stButton"] > button[kind="primary"] {
    color: #1A73E8 !important;
    font-weight: 700 !important;
}
/* hover 시 옅은 배경으로 클릭 가능 표시 */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stButton"] > button:hover {
    background: #EEF3FB !important;
    color: #1A73E8 !important;
}
/* 대화 제목: 왼쪽 정렬 (첫 번째 컬럼 버튼만, 삭제 버튼은 그대로) */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child
  [data-testid="stButton"] > button {
    justify-content: flex-start !important;
    text-align: left !important;
}
/* 버튼 내부 라벨 래퍼(button > div)가 flex 가운데 정렬 → 왼쪽으로 */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:first-child
  [data-testid="stButton"] > button > div {
    justify-content: flex-start !important;
    width: 100% !important;
}
/* 대화 목록 스크롤 컨테이너: 테두리 제거 */
[data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {
    border: none !important;
}
[data-testid="stChatMessage"] {
    background-color: transparent !important;
    box-shadow: none !important;
    border: none !important;
    padding: 4px 0;
    align-items: flex-start;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stVerticalBlock"] {
    background-color: #EFF6FF;
    border-radius: 18px 18px 18px 4px;
    padding: 12px 18px;
    max-width: 72%;
    line-height: 1.6;
    margin-right: auto;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageAvatarUser"] { display: none; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [data-testid="stVerticalBlock"] {
    background-color: transparent;
    padding: 0 8px;
    max-width: 82%;
    line-height: 1.7;
    margin-right: auto;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [data-testid="stChatMessageAvatarAssistant"] { display: none; }

/* ── 액션 버튼 ─────────────────────────────────────────────────── */
.msg-actions { display: flex; gap: 4px; margin-top: 6px; }
.action-btn {
    display: flex; align-items: center; justify-content: center;
    width: 32px; height: 32px; padding: 0;
    border: 1px solid #DADCE0; border-radius: 6px;
    background: #FFFFFF; color: #5F6368;
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.action-btn:hover { border-color: #1A73E8; background: #E8F0FE; color: #1A73E8; }
.action-btn svg { width: 14px; height: 14px; pointer-events: none; }
/* 숨겨진 retry st.button — JS .click()으로만 트리거 */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [data-testid="stButton"] {
    position: absolute !important;
    opacity: 0 !important;
    pointer-events: none !important;
    width: 1px !important; height: 1px !important;
    overflow: hidden !important;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [data-testid="stButton"] button { pointer-events: auto !important; }

/* ── 입력창 ────────────────────────────────────────────────────── */
[data-testid="stChatInput"] { margin-right: 150px !important; }
[data-testid="stChatInput"] > div {
    border-radius: 24px !important;
    border: 1.5px solid #D3E3FD !important;
    background-color: #FFFFFF !important;
    box-shadow: 0 2px 8px rgba(60,64,67,0.10) !important;
}
[data-testid="stChatInput"] textarea {
    background-color: transparent !important;
    padding-left: 16px !important;
}
[data-testid="stChatInputSubmitButton"][disabled] { color: rgba(49,51,63,0.55) !important; }
[data-testid="stChatInputSubmitButton"]:not([disabled]) {
    color: #1A73E8 !important;
    background: #E8F0FE !important;
}

/* ── 은행 링크 ─────────────────────────────────────────────────── */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) a {
    color: #1A73E8; text-decoration: none; font-weight: 500;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) a:hover {
    text-decoration: underline;
}

/* ── 모델 팝오버 ────────────────────────────────────────────────── */
[data-testid="stPopover"] {
    position: fixed !important;
    right: 90px !important;
    bottom: 56px !important;
    width: fit-content !important;
    z-index: 999 !important;
}
[data-testid="stPopoverButton"] {
    width: auto !important; min-width: 0 !important;
    height: 59px !important;
    background-color: #E8F0FE !important;
    border: 1.5px solid #C5D9F8 !important;
    border-radius: 24px !important;
    padding: 0 16px !important;
    font-size: 0.8rem !important; font-weight: 500 !important;
    color: #1A73E8 !important;
    white-space: nowrap !important;
}
[data-testid="stPopoverBody"] [data-testid="stButton"] > button {
    border-radius: 8px !important;
    font-size: 0.85rem !important;
    min-height: 2.2rem !important;
    padding: 5px 12px !important;
    text-align: left !important;
    box-shadow: none !important;
}
[data-testid="stPopoverBody"] [data-testid="stButton"] > button[kind="secondary"] {
    border: none !important; background: transparent !important; color: #3C4043 !important;
}
[data-testid="stPopoverBody"] [data-testid="stButton"] > button[kind="secondary"]:hover {
    background: #F1F3F4 !important; color: #1A73E8 !important;
}
[data-testid="stPopoverBody"] [data-testid="stButton"] > button[kind="primary"] {
    border: none !important; background: #EFF6FF !important;
    color: #1565C0 !important; font-weight: 600 !important;
}
[data-testid="stPopoverBody"] [data-testid="stButton"] > button[kind="primary"]:hover {
    background: #D3E3FD !important; color: #1565C0 !important;
}
/* 파일 첨부: 안내 영역 아래에 버튼이 오도록 세로 배치 */
[data-testid="stFileUploaderDropzone"] {
    flex-direction: column !important;
    align-items: stretch !important;
    gap: 8px !important;
}
[data-testid="stFileUploaderDropzone"] button {
    width: 100% !important;
    margin-left: 0 !important;
}
</style>""", unsafe_allow_html=True)

# ── Phoenix 초기화 ────────────────────────────────────────────────────
_phoenix = tracing.init_tracing()

# ── 세션: 대화 ──────────────────────────────────────────────────────
if "conversation" not in st.session_state:
    st.session_state.conversation = storage.new_conversation()

# ── 세션: 설정 (config에서 한 번만 로드) ─────────────────────────────
if "sel_provider" not in st.session_state:
    _cfg = config.load()
    _providers = list(llm.PROVIDERS.keys())
    _prov = _cfg.get("provider", "")
    st.session_state.sel_provider = _prov if _prov in _providers else _providers[0]
    st.session_state.sel_web_search = bool(_cfg.get("web_search", False))
    st.session_state.sel_cache_enabled = bool(_cfg.get("cache_enabled", False))
    _ds = _cfg.get("default_style", list(llm.TEMP_OPTIONS.keys())[0])
    st.session_state.sel_temperature = llm.TEMP_OPTIONS.get(_ds, 0.7)
    st.session_state.sel_system_prompt = _cfg.get("system_prompt", "")
    if "selected_model" not in st.session_state:
        _models = llm.models_for(st.session_state.sel_provider)
        _dm = _cfg.get("default_model", "")
        st.session_state.selected_model = _dm if _dm in _models else _models[0]


# 사이트 백엔드(이용 통계) 주소
_BACKEND_URL = "http://localhost:8000"

# 은행 에이전트 인증 토큰: 사이트 iframe(?token=)이 있으면 그걸 단일 기준으로 매 실행 동기화
# (로그인=토큰, 로그아웃=빈 토큰). token 파라미터가 아예 없을 때(:8501 직접 접속)만 사이드바 로그인 사용.
_SITE_EMBEDDED = "token" in st.query_params
if _SITE_EMBEDDED:
    st.session_state.auth_token = (st.query_params.get("token") or "").strip() or None
elif "auth_token" not in st.session_state:
    st.session_state.auth_token = None


def _fetch_user_name(token: str) -> str | None:
    """토큰으로 로그인 사용자 이름 조회(홈 인사말용)."""
    try:
        r = requests.get(f"{_BACKEND_URL}/api/me",
                         headers={"Authorization": f"Bearer {token}"}, timeout=3)
        if r.ok:
            return (r.json().get("name") or "").strip() or None
    except Exception:
        pass
    return None


# 토큰이 바뀔 때만 이름을 조회해 캐시(매 실행 호출 방지)
_tok = st.session_state.auth_token
if _tok and st.session_state.get("_auth_name_for") != _tok:
    st.session_state.auth_name = _fetch_user_name(_tok)
    st.session_state._auth_name_for = _tok
elif not _tok:
    st.session_state.auth_name = None
    st.session_state._auth_name_for = None


def _log_search_products(doc_names: list[str]) -> None:
    """RAG가 조회한 FSS 상품(doc_name)을 이용 통계로 로깅한다.

    doc_name 규칙: 'FSS_{카테고리}_{은행}_{상품명}' (fss_fetcher).
    실패해도 챗 동작에는 영향을 주지 않는다.
    """
    items = []
    seen = set()
    for dn in doc_names:
        if not dn.startswith("FSS_"):
            continue
        parts = dn.split("_", 3)  # ['FSS', category, bank, product]
        if len(parts) < 4:
            continue
        _, category, bank, product = parts
        key = (bank, product)
        if key in seen:
            continue
        seen.add(key)
        items.append({"bank": bank, "product": product, "category": category})
    if not items:
        return
    try:
        import urllib.request

        req = urllib.request.Request(
            f"{_BACKEND_URL}/api/track/search",
            data=_json.dumps({"items": items}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass  # 통계 로깅 실패는 무시


def get_api_key(provider: str) -> str | None:
    key_name = llm.secret_key_for(provider)
    cfg_key = key_name.lower()
    value = config.load().get(cfg_key, "")
    if value := (value or "").strip():
        return value
    try:
        return (st.secrets.get(key_name, "") or "").strip() or None
    except FileNotFoundError:
        return None


def process_files(files) -> list[dict]:
    """업로드된 파일을 llm.stream_chat 용 attachments 형식으로 변환."""
    attachments = []
    for f in files:
        name = f.name
        mime = f.type or ""
        data = f.read()
        ext = name.lower().rsplit(".", 1)[-1]
        is_image = mime.startswith("image/") or ext in ("png", "jpg", "jpeg", "gif", "webp")
        if is_image:
            if not mime.startswith("image/"):
                mime = f"image/{ext}"
            attachments.append({"type": "image", "name": name, "data": data, "mime_type": mime})
        elif ext == "pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(data))
                text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
                attachments.append({"type": "text", "name": name, "content": text})
            except Exception as e:
                attachments.append({"type": "text", "name": name, "content": f"[PDF 읽기 오류: {e}]"})
        else:
            attachments.append({"type": "text", "name": name,
                                "content": data.decode("utf-8", errors="replace")})
    return attachments


# ── 사이드바 ────────────────────────────────────────────────────────
# 상단 'AI챗봇' 제목은 CSS(stSidebarHeader::before)로 숨기기 버튼 행에 표시된다.
with st.sidebar:
    if st.button("새 채팅", icon=":material/add:", use_container_width=True):
        storage.save_conversation(st.session_state.conversation)
        st.session_state.conversation = storage.new_conversation()
        st.rerun()

    st.caption("이전 대화")
    current_id = st.session_state.conversation["id"]
    _convos = storage.list_conversations()
    # 대화가 많으면 독립 스크롤 영역으로 → 목록이 늘어나도 더 많이 탐색 가능.
    # (적을 때는 자연 높이로 두어 빈 상자가 생기지 않게 함)
    _list_box = st.container(height=340) if len(_convos) > 7 else st.container()
    with _list_box:
        for meta in _convos:
            is_current = meta["id"] == current_id
            cols = st.columns([0.82, 0.18])
            # 말풍선 아이콘 없이 제목만. 현재 대화는 primary 타입으로 구분.
            if cols[0].button(
                meta["title"],
                key=f"open_{meta['id']}",
                use_container_width=True,
                type="primary" if is_current else "secondary",
            ):
                storage.save_conversation(st.session_state.conversation)
                loaded = storage.load_conversation(meta["id"])
                if loaded:
                    st.session_state.conversation = loaded
                    st.rerun()
            if cols[1].button("", icon=":material/delete:", key=f"del_{meta['id']}", use_container_width=True):
                storage.delete_conversation(meta["id"])
                if is_current:
                    st.session_state.conversation = storage.new_conversation()
                st.rerun()

    st.divider()

    uploaded_files = st.file_uploader(
        ":material/attach_file: 파일 첨부",
        accept_multiple_files=True,
        type=["txt", "md", "py", "js", "ts", "csv", "json", "html", "css",
              "pdf", "png", "jpg", "jpeg", "gif", "webp"],
        help="텍스트·PDF는 내용을 추출해 LLM에 전달, 이미지는 멀티모달로 전달합니다.",
    )

    # 은행 에이전트 로그인 — 사이트 iframe 임베드 시에는 사이트 로그인과 자동 연동되므로
    # 사이드바에 노출하지 않고, :8501 직접 접속 시에만 수동 로그인 UI를 보여준다.
    if not _SITE_EMBEDDED:
        st.divider()
        with st.expander("🤖 은행 에이전트", expanded=not st.session_state.get("auth_token")):
            if st.session_state.get("auth_token"):
                st.success("로그인됨 — 계좌·거래내역·이체·문의 도구 사용 가능")
                if st.button("로그아웃", use_container_width=True, key="agent_logout"):
                    st.session_state.auth_token = None
                    st.rerun()
            else:
                st.caption("로그인하면 계좌·이체 등 은행업무를 대화로 처리합니다. (테스트 계정: demo / demo1234)")
                _lu = st.text_input("아이디", key="agent_login_u")
                _lp = st.text_input("비밀번호", type="password", key="agent_login_p")
                if st.button("에이전트 로그인", type="primary", use_container_width=True, key="agent_login_btn"):
                    try:
                        _r = requests.post(f"{_BACKEND_URL}/api/login",
                                           json={"username": _lu, "password": _lp}, timeout=5)
                        if _r.ok:
                            st.session_state.auth_token = _r.json()["token"]
                            st.rerun()
                        else:
                            st.error(_r.json().get("detail", "로그인에 실패했습니다."))
                    except Exception as _e:
                        st.error(f"백엔드(:8000) 연결 실패: {_e}")


# ── 런타임 설정 ──────────────────────────────────────────────────────
provider = st.session_state.sel_provider
temperature = st.session_state.sel_temperature
system_prompt = st.session_state.sel_system_prompt
web_search_enabled = st.session_state.sel_web_search
cache_enabled = st.session_state.sel_cache_enabled
openai_key = get_api_key("OpenAI")
rag_enabled = bool(openai_key)

# 은행 에이전트: 로그인 토큰(iframe ?token= 또는 사이드바 로그인)이 있고 OpenAI면 활성화
auth_token = st.session_state.get("auth_token")
agent_enabled = bool(auth_token) and provider == "OpenAI" and bool(openai_key)

# ── API 키 확인 ─────────────────────────────────────────────────────
api_key = get_api_key(provider)
if not api_key:
    key_name = llm.secret_key_for(provider)
    st.error(
        f"**{provider} API 키가 설정되지 않았습니다.**\n\n"
        f"⚙️ 설정 페이지에서 입력하거나 `.streamlit/secrets.toml` 에 추가하세요:\n\n"
        f"```toml\n{key_name} = \"여기에-키-입력\"\n```"
    )
    st.stop()


# ── 메인: 대화 렌더링 ────────────────────────────────────────────────
conv = st.session_state.conversation

if not conv["messages"]:
    import html as _html
    _name = st.session_state.get("auth_name")
    _greet = (f"{_html.escape(_name)}님 안녕하세요, 무엇을 도와드릴까요?"
              if _name else "안녕하세요, 무엇을 도와드릴까요?")
    st.markdown("<div style='height:22vh'></div>", unsafe_allow_html=True)
    st.markdown(f"""
<div style='text-align:center; padding:1.5rem 2rem;'>
  <p style='font-size:2rem; font-weight:400; color:#3C4043;
            letter-spacing:-0.3px; line-height:1.35; margin:0;'>
    {_greet}
  </p>
</div>""", unsafe_allow_html=True)
    st.markdown("<div style='height:8vh'></div>", unsafe_allow_html=True)

for i, msg in enumerate(conv["messages"]):
    with st.chat_message(msg["role"]):
        _display = _inject_bank_links(msg["content"]) if msg["role"] == "assistant" else msg["content"]
        st.markdown(_display)

        if msg["role"] == "assistant":
            _cb64 = _base64.b64encode(msg["content"].encode()).decode()
            st.markdown(f"""
<div class='msg-actions'>
  <button class='action-btn' title='좋아요'>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/><path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
  </button>
  <button class='action-btn' title='싫어요'>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z"/><path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg>
  </button>
  <button class='action-btn' title='다시 시도'>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-5"/></svg>
  </button>
  <button class='action-btn' title='복사' data-b64="{_cb64}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
  </button>
</div>""", unsafe_allow_html=True)
            if st.button("↺", key=f"retry_{i}"):
                conv["messages"] = conv["messages"][:i]
                storage.save_conversation(conv)
                if conv["messages"] and conv["messages"][-1]["role"] == "user":
                    st.session_state["_retry_prompt"] = conv["messages"][-1]["content"]
                st.rerun()

# ── 에이전트 이체 확인 카드 (사용자 확인 후에만 실제 이체 실행) ──────
_pending = st.session_state.get("pending_transfer")
if _pending:
    with st.chat_message("assistant"):
        st.markdown(
            "**💸 이체 확인**\n\n"
            f"- 받는 분: {_pending['holder_name']} ({_pending['bank_name']} {_pending['to_account']})\n"
            f"- 출금 계좌: {_pending['from_account']}\n"
            f"- 이체 금액: **{_pending['amount']:,}원**\n"
            f"- 수수료: {_pending['fee']:,}원"
        )
        _c1, _c2 = st.columns(2)
        if _c1.button("✅ 이체 실행", type="primary", key="tf_exec"):
            _res = agent.execute_transfer(_pending, auth_token)
            st.session_state.pop("pending_transfer", None)
            if "error" in _res:
                _msg = f"이체에 실패했습니다: {_res['error']}"
            else:
                _msg = (f"✅ 이체가 접수되었습니다. 거래번호 {_res.get('transfer_id')}, "
                        f"수수료 {_res.get('fee', 0):,}원.")
            conv["messages"].append({"role": "assistant", "content": _msg})
            storage.save_conversation(conv)
            st.rerun()
        if _c2.button("취소", key="tf_cancel"):
            st.session_state.pop("pending_transfer", None)
            conv["messages"].append({"role": "assistant", "content": "이체를 취소했습니다."})
            storage.save_conversation(conv)
            st.rerun()

# ── 액션 버튼 이벤트 핸들러 주입 (iframe → 부모 DOM) ────────────────
_components.html("""<script>
(function(){
  function attach(){
    var pd=window.parent.document;
    Array.prototype.forEach.call(
      pd.querySelectorAll('.action-btn:not([data-ev])'),
      function(btn){
        btn.setAttribute('data-ev','1');
        btn.addEventListener('click',function(){
          var self=this;
          var title=self.getAttribute('title')||'';
          function flash(){
            self.style.color='#1A73E8';
            self.style.borderColor='#1A73E8';
            self.style.background='#E8F0FE';
            setTimeout(function(){
              self.style.color='';
              self.style.borderColor='';
              self.style.background='';
            },2000);
          }
          if(title==='복사'){
            try{
              var b64=self.getAttribute('data-b64')||'';
              var bytes=Uint8Array.from(atob(b64),function(c){return c.charCodeAt(0);});
              var text=new TextDecoder('utf-8').decode(bytes);
              window.parent.navigator.clipboard.writeText(text).then(flash,function(){
                try{
                  var ta=pd.createElement('textarea');
                  ta.value=text;
                  ta.style.cssText='position:fixed;top:-999px;left:-999px;width:1px;height:1px;opacity:0';
                  pd.body.appendChild(ta);ta.focus();ta.select();
                  pd.execCommand('copy');pd.body.removeChild(ta);
                  flash();
                }catch(e2){}
              });
            }catch(e){}
          }else if(title==='다시 시도'){
            var msg=self.closest('[data-testid="stChatMessage"]');
            if(msg){
              var rb=msg.querySelector('[data-testid="stButton"] button');
              if(rb){rb.click();flash();}
            }
          }else{
            flash();
          }
        });
      }
    );
  }
  attach();
  new MutationObserver(attach).observe(
    window.parent.document.body,{childList:true,subtree:true}
  );
})();
</script>""", height=0)

# ── 모델 팝오버 ─────────────────────────────────────────────────────
model = st.session_state.selected_model
_models = llm.models_for(provider)
if model not in _models:
    model = _models[0]
    st.session_state.selected_model = model

with st.popover(model):
    st.caption("모델 선택")
    for _m in _models:
        _btn_type = "primary" if _m == model else "secondary"
        if st.button(_m, key=f"mpick_{_m}", use_container_width=True, type=_btn_type):
            st.session_state.selected_model = _m
            st.rerun()

# ── 사용자 입력 처리 ─────────────────────────────────────────────────
_retry_prompt = st.session_state.pop("_retry_prompt", None)
_chat_input = st.chat_input("메시지를 입력하세요…")
prompt = _chat_input or _retry_prompt

if prompt:
    if _chat_input:
        conv["messages"].append({"role": "user", "content": prompt})

    # ── 은행업무 에이전트 경로 (로그인 + OpenAI) ──────────────────────
    if agent_enabled:
        if not _retry_prompt:
            with st.chat_message("user"):
                st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("🤖 처리 중…"):
                try:
                    result = agent.run_agent(
                        conv["messages"], openai_key=openai_key, model=model,
                        token=auth_token, system_prompt=system_prompt.strip() or None,
                    )
                except Exception as e:
                    result = {"kind": "message", "text": f"처리 중 오류가 발생했습니다: {e}"}
            st.markdown(result["text"])
        conv["messages"].append({"role": "assistant", "content": result["text"]})
        if result["kind"] == "transfer_proposal":
            st.session_state.pending_transfer = result["proposal"]
        storage.save_conversation(conv)
        st.rerun()

    llm_messages = [m.copy() for m in conv["messages"]]

    search_ctx = ""
    if web_search_enabled:
        with st.spinner("🔍 웹 검색 중…"):
            results = search.web_search(prompt)
            search_ctx = search.format_for_llm(results)
        if search_ctx:
            llm_messages[-1]["content"] = search_ctx + "\n사용자 질문: " + prompt

    cached_answer = None
    if cache_enabled and openai_key:
        with st.spinner("⚡ 캐시 확인 중…"):
            cached_answer = cache.check(prompt, openai_key)

    if cached_answer:
        if not _retry_prompt:
            with st.chat_message("user"):
                st.markdown(prompt)
                if uploaded_files:
                    st.caption("📎 " + ", ".join(f.name for f in uploaded_files))
        with st.chat_message("assistant"):
            st.markdown(cached_answer)
        conv["messages"].append({"role": "assistant", "content": cached_answer})
        storage.save_conversation(conv)
        st.rerun()

    rag_ctx = ""
    if rag_enabled and openai_key:
        with st.spinner("📚 문서 검색 중…"):
            rag_ctx = rag.search(prompt, openai_key)
        if rag_ctx:
            llm_messages[-1]["content"] = rag_ctx + "\n\n" + llm_messages[-1]["content"]
        # 이용 통계: 조회된 FSS 상품을 백엔드에 로깅 (실패해도 챗 동작엔 영향 없음)
        _log_search_products(rag.last_doc_names())

    attachments = process_files(uploaded_files) if uploaded_files else []

    if not _retry_prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
            if uploaded_files:
                st.caption("📎 " + ", ".join(f.name for f in uploaded_files))
            if search_ctx:
                with st.expander("🔍 검색 결과 보기"):
                    st.text(search_ctx)
            if rag_ctx:
                with st.expander("📚 참고 문서 보기"):
                    st.text(rag_ctx)

    with st.chat_message("assistant"):
        try:
            response = st.write_stream(
                llm.stream_chat(
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    messages=llm_messages,
                    temperature=temperature,
                    system_prompt=system_prompt.strip() or None,
                    attachments=attachments,
                )
            )
        except Exception as e:
            response = None
            st.error(f"응답 생성 중 오류가 발생했습니다:\n{e}")

    if response:
        conv["messages"].append({"role": "assistant", "content": response})
        storage.save_conversation(conv)
        if cache_enabled and openai_key:
            cache.store(prompt, response, openai_key)
        st.rerun()
