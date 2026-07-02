"""인프라/API 키 설정 — 운영자 전용 유지보수 스크립트.

일반 사용자에게 노출되지 않도록 app.py의 pages/ 멀티페이지 구성에서 제외되어 있다.
필요할 때 `streamlit run settings_page.py`로 직접 실행한다.
챗봇 동작 설정(제공자·모델·답변스타일·시스템프롬프트·웹검색)은 Backoffice > 성능관리에서 관리한다.
"""
from __future__ import annotations

import io

import streamlit as st
import cache
import config
import fss_fetcher
import rag
import tracing

st.set_page_config(page_title="설정", page_icon="⚙️", layout="wide")


def _init_session() -> None:
    if "sel_cache_enabled" in st.session_state:
        return
    _cfg = config.load()
    st.session_state.sel_cache_enabled = bool(_cfg.get("cache_enabled", False))


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


# ── 헤더 ─────────────────────────────────────────────────────────────
st.title("⚙️ 설정 (운영자 전용)")
st.caption("이 화면은 일반 사용자에게 노출되지 않는 유지보수용 화면입니다.")
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

# ── AI 모델 / 챗봇 동작 ─────────────────────────────────────────────
st.info(
    "🤖 제공자·기본 모델·답변 스타일·시스템 프롬프트·웹검색 사용여부는 "
    "Backoffice 관리자 페이지 > 성능관리 탭에서 관리합니다."
)

st.divider()

# ── 채팅 기능 ────────────────────────────────────────────────────────
st.header("🔧 채팅 기능")
st.checkbox(
    "⚡ 시맨틱 캐시",
    key="sel_cache_enabled",
    disabled=not _openai_key,
    help="동일/유사한 질문은 저장된 답변을 반환합니다. (OpenAI 키 필요)",
)

st.divider()

# ── 저장 ─────────────────────────────────────────────────────────────
if st.button("💾 설정 저장", type="primary", use_container_width=True):
    config.save({
        **cfg,
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
