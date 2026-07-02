"""설정 페이지."""
from __future__ import annotations

import io
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import streamlit as st
import cache
import config
import fss_fetcher
import llm
import rag
import tracing

st.set_page_config(page_title="설정", page_icon="⚙️", layout="wide")

st.markdown(
    "<style>[data-testid='stSidebarNav']{display:none;}"
    "[data-testid='stSidebar']{background-color:#F8FAFD;border-right:1px solid #DDE3EA;}</style>",
    unsafe_allow_html=True,
)

_TEMP_OPTIONS = {
    "🎯 정확  — 코드·번역·사실 질문": 0.2,
    "⚖️ 균형  — 일반 대화 (기본)": 0.7,
    "✨ 창의  — 글쓰기·아이디어": 0.95,
}


def _init_session() -> None:
    if "sel_provider" in st.session_state:
        return
    _cfg = config.load()
    _providers = list(llm.PROVIDERS.keys())
    _prov = _cfg.get("provider", "")
    st.session_state.sel_provider = _prov if _prov in _providers else _providers[0]
    st.session_state.sel_web_search = bool(_cfg.get("web_search", False))
    st.session_state.sel_cache_enabled = bool(_cfg.get("cache_enabled", False))
    _ds = _cfg.get("default_style", list(_TEMP_OPTIONS.keys())[0])
    st.session_state.sel_temperature = _TEMP_OPTIONS.get(_ds, 0.7)
    st.session_state.sel_system_prompt = _cfg.get("system_prompt", "")
    if "selected_model" not in st.session_state:
        _models = llm.models_for(st.session_state.sel_provider)
        _dm = _cfg.get("default_model", "")
        st.session_state.selected_model = _dm if _dm in _models else _models[0]


_init_session()


def _get_openai_key() -> str | None:
    cfg = config.load()
    k = cfg.get("openai_api_key", "").strip()
    if k:
        return k
    try:
        return (st.secrets.get("OPENAI_API_KEY", "") or "").strip() or None
    except FileNotFoundError:
        return None


# ── 사이드바 ─────────────────────────────────────────────────────────
with st.sidebar:
    st.page_link(
        "app.py",
        label="AI 채팅",
        icon=":material/chat_bubble_outline:",
        use_container_width=True,
    )
    st.divider()
    st.page_link(
        "pages/2_⚙️_설정.py",
        label="설정",
        icon=":material/settings:",
        use_container_width=True,
    )


# ── 헤더 ─────────────────────────────────────────────────────────────
st.title("⚙️ 설정")
st.page_link("app.py", label="← 채팅으로 돌아가기")
st.write("")

cfg = config.load()
_openai_key = _get_openai_key()

# ── 지식베이스 관리 (RAG) ─────────────────────────────────────────────
st.header("📚 지식베이스 (RAG)")
if not _openai_key:
    st.warning("RAG 기능은 OpenAI API 키가 필요합니다. 아래 API 키 섹션에서 입력 후 저장하세요.")
else:
    _rag_files = st.file_uploader(
        "문서 추가",
        type=["txt", "md", "pdf", "py", "js", "ts", "csv", "json", "html", "css"],
        accept_multiple_files=True,
        key="rag_uploader",
    )
    if _rag_files and st.button("📥 인덱싱", use_container_width=True):
        for f in _rag_files:
            data = f.read()
            ext = f.name.lower().rsplit(".", 1)[-1]
            if ext == "pdf":
                try:
                    import pypdf
                    reader = pypdf.PdfReader(io.BytesIO(data))
                    text = "\n\n".join(p.extract_text() or "" for p in reader.pages)
                except Exception as e:
                    text = f"[PDF 읽기 오류: {e}]"
            else:
                text = data.decode("utf-8", errors="replace")
            with st.spinner(f"{f.name} 인덱싱 중…"):
                n = rag.index_document(f.name, text, _openai_key)
            st.success(f"{f.name} ({n}개 청크)")
        st.rerun()

    docs = rag.list_documents()
    if docs:
        st.caption("인덱싱된 문서")
        for doc in docs:
            c1, c2 = st.columns([0.85, 0.15])
            c1.caption(doc)
            if c2.button("🗑", key=f"rag_del_{doc}", use_container_width=True):
                rag.remove_document(doc)
                st.rerun()
    else:
        st.caption("아직 인덱싱된 문서가 없습니다.")

st.divider()

# ── FSS 금융상품 데이터 ───────────────────────────────────────────────
st.header("🏦 FSS 금융상품 데이터")
_fss_default = cfg.get("fss_api_key", "")
if not _fss_default:
    try:
        _fss_default = st.secrets.get("FSS_API_KEY", "") or ""
    except Exception:
        _fss_default = ""

fss_key_input = st.text_input(
    "FSS API 키",
    value=_fss_default,
    type="password",
    placeholder="finlife.fss.or.kr 발급 32자 키",
)
selected_groups = st.multiselect(
    "금융권역 선택",
    options=list(fss_fetcher.FIN_GROUPS.keys()),
    default=["은행"],
    help="저축은행·신협·새마을금고 포함 시 데이터가 많아 수집 시간이 늘어납니다.",
)
fin_group_codes = [fss_fetcher.FIN_GROUPS[g] for g in selected_groups] or ["020000"]
if st.button("📥 FSS 데이터 가져오기", use_container_width=True, disabled=not fss_key_input):
    if not _openai_key:
        st.error("FSS 데이터 인덱싱은 OpenAI API 키가 필요합니다.")
    else:
        with st.spinner("FSS API에서 데이터 가져오는 중…"):
            try:
                products_by_category = fss_fetcher.fetch_all(fss_key_input, fin_groups=fin_group_codes)
            except Exception as e:
                st.error(f"FSS API 오류: {e}")
                products_by_category = {}
        total = 0
        for category, docs_list in products_by_category.items():
            if not docs_list:
                continue
            prog = st.progress(0, text=f"{category} 인덱싱 중…")
            for idx, (doc_name, text) in enumerate(docs_list):
                rag.index_document(doc_name, text, _openai_key)
                prog.progress((idx + 1) / len(docs_list))
            st.success(f"{category}: {len(docs_list)}개 상품 완료")
            total += len(docs_list)
        if total:
            cache.clear()
            st.info(f"총 {total}개 상품이 지식베이스에 추가되었습니다.")
            st.rerun()

st.divider()

# ── API 키 ────────────────────────────────────────────────────────────
st.header("🔑 API 키")
st.caption("저장하면 secrets.toml 없이도 동작합니다.")
openai_key_input = st.text_input(
    "OpenAI API Key",
    value=cfg.get("openai_api_key", ""),
    type="password",
    placeholder="sk-proj-...",
)

st.divider()

# ── 고급 설정 ─────────────────────────────────────────────────────────
with st.expander("⚙️ 고급 설정"):
    rag_top_k = st.slider(
        "RAG 검색 결과 수 (top_k)",
        min_value=3, max_value=20,
        value=int(cfg.get("rag_top_k", 10)),
        help="질문당 참고할 문서 청크 수.",
    )
    col1, col2 = st.columns(2)
    with col1:
        redis_ttl = st.number_input(
            "Redis TTL (초)",
            min_value=60, max_value=86400,
            value=int(cfg.get("redis_ttl", 1800)), step=60,
        )
    with col2:
        cache_threshold = st.slider(
            "캐시 유사도 임계값",
            min_value=0.05, max_value=0.30,
            value=float(cfg.get("cache_threshold", 0.08)),
            step=0.01, format="%.2f",
        )
    col3, col4, col5 = st.columns([2, 1, 1])
    with col3:
        es_host = st.text_input("Elasticsearch 주소", value=cfg.get("es_host", "http://localhost:9200"))
    with col4:
        redis_host = st.text_input("Redis 주소", value=cfg.get("redis_host", "localhost"))
    with col5:
        redis_port = st.number_input(
            "Redis 포트", min_value=1, max_value=65535,
            value=int(cfg.get("redis_port", 6379)),
        )

st.divider()

# ── 시스템 상태 ───────────────────────────────────────────────────────
st.header("📊 시스템 상태")
_phoenix = tracing.init_tracing()
col_ph, col_rd = st.columns(2)
with col_ph:
    if _phoenix["phoenix_up"]:
        st.success("🔭 Phoenix 추적: 연결됨")
        st.markdown(f"[대시보드 열기]({_phoenix['url']})")
    else:
        st.warning("🔭 Phoenix 추적: 꺼짐")
        st.code(".venv/bin/phoenix serve", language="bash")
with col_rd:
    if cache._get_redis() is not None:
        st.success("🗄 Redis 캐시: 연결됨")
    else:
        st.warning("🗄 Redis 캐시: 꺼짐")

_stats = cache.stats()
col_a, col_b = st.columns(2)
col_a.metric("Redis 캐시 항목", f"{_stats['redis']}개")
col_b.metric("ChromaDB 캐시 항목", f"{_stats['chromadb']}개")
if st.button("🗑 캐시 전체 초기화", use_container_width=True):
    cache.clear()
    st.success("캐시가 초기화되었습니다.")
    st.rerun()

st.divider()

# ── AI 모델 ──────────────────────────────────────────────────────────
st.header("🤖 AI 모델")

_providers = list(llm.PROVIDERS.keys())
_prov_idx = _providers.index(st.session_state.sel_provider) if st.session_state.sel_provider in _providers else 0
col_prov, col_model = st.columns(2)
with col_prov:
    provider = st.selectbox("제공자", _providers, index=_prov_idx, key="sel_provider")
with col_model:
    _models = llm.models_for(provider)
    _m_idx = 0
    if "selected_model" in st.session_state and st.session_state.selected_model in _models:
        _m_idx = _models.index(st.session_state.selected_model)
    st.selectbox("기본 모델", _models, index=_m_idx, key="selected_model")

_style_keys = list(_TEMP_OPTIONS.keys())
_curr_style = next(
    (k for k, v in _TEMP_OPTIONS.items() if v == st.session_state.get("sel_temperature", 0.7)),
    _style_keys[1],
)
_style_idx = _style_keys.index(_curr_style) if _curr_style in _style_keys else 1
col_style, _ = st.columns([2, 1])
with col_style:
    _new_style = st.selectbox("답변 스타일", _style_keys, index=_style_idx)
st.session_state.sel_temperature = _TEMP_OPTIONS[_new_style]

st.text_area(
    "시스템 프롬프트",
    placeholder="예: 당신은 친절한 한국어 비서입니다.",
    height=100,
    key="sel_system_prompt",
)

st.divider()

# ── 채팅 기능 ────────────────────────────────────────────────────────
st.header("🔧 채팅 기능")
col_ws, col_sc = st.columns(2)
with col_ws:
    st.checkbox(
        "🔍 웹 검색",
        key="sel_web_search",
        help="답변 전 DuckDuckGo로 최신 정보를 검색합니다. (API 키 불필요)",
    )
with col_sc:
    st.checkbox(
        "⚡ 시맨틱 캐시",
        key="sel_cache_enabled",
        disabled=not _openai_key,
        help="동일/유사한 질문은 저장된 답변을 반환합니다. (OpenAI 키 필요)",
    )

st.divider()

# ── 저장 ─────────────────────────────────────────────────────────────
if st.button("💾 설정 저장", type="primary", use_container_width=True):
    _sel_style = next(
        (k for k, v in _TEMP_OPTIONS.items() if v == st.session_state.sel_temperature),
        list(_TEMP_OPTIONS.keys())[1],
    )
    config.save({
        **cfg,
        "provider":        st.session_state.sel_provider,
        "default_model":   st.session_state.selected_model,
        "default_style":   _sel_style,
        "system_prompt":   st.session_state.sel_system_prompt,
        "web_search":      st.session_state.sel_web_search,
        "cache_enabled":   st.session_state.sel_cache_enabled,
        "openai_api_key":  openai_key_input.strip(),
        "fss_api_key":     fss_key_input.strip(),
        "rag_top_k":       rag_top_k,
        "cache_threshold": cache_threshold,
        "redis_ttl":       redis_ttl,
        "es_host":         es_host.strip(),
        "redis_host":      redis_host.strip(),
        "redis_port":      int(redis_port),
    })
    st.success("✅ 설정이 저장되었습니다.")
    st.toast("설정 저장 완료!", icon="✅")
