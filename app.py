"""ChatGPT/Gemini 스타일 채팅 웹앱 (Streamlit).

실행:  streamlit run app.py
"""

from __future__ import annotations
import io
import json as _json
import time
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
    """어시스턴트 응답에서 기존 링크(상품명 등)는 일반 텍스트로 풀고, 은행명에만 홈페이지 ↗ 링크를 단다."""
    # '상품 페이지' 링크 줄 제거
    result = _re.sub(r'\n[ \t]*[-*○◦•]?\s*\[상품\s*페이지\]\([^)]*\)', '', text)
    # 기존 마크다운 링크는 텍스트만 남기고 링크 제거 → 상품명 등에 걸린 링크 제거
    result = _re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', result)
    # 은행명에만 홈페이지 링크 주입(긴 이름부터 처리해 부분일치 방지)
    for bank, url in sorted(_BANK_URLS.items(), key=lambda x: -len(x[0])):
        result = _re.sub(
            rf'(?<!\w){_re.escape(bank)}(?!\w)',
            lambda m, u=url: f'[{m.group(0)} ↗]({u})',
            result,
        )
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
# 매치뱅크 사이트(site/css/style.css)와 동일한 브랜드 그린 팔레트로 통일.
# Streamlit은 별도 iframe(문서)라 site의 CSS 변수를 상속받지 못하므로, 여기서 같은
# 값으로 :root 변수를 다시 선언해 사이트와 동일한 이름(--blue 등)으로 맞춘다.
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap');
:root {
    --blue:        #0FA968;
    --blue-dark:   #0B8457;
    --blue-soft:   #E3F6EC;
    --blue-line:   #A8E0C4;
    --bg-soft:     #F8FAFD;
    --border:      #DDE3EA;
    --text:        #3C4043;
    --text-sub:    #5F6368;
}
.stApp, .stApp [class*="css"] {
    font-family: "IBM Plex Sans KR", -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif !important;
}
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
    /* 배경색으로 구분하지 않고, 옅은 경계선만으로 메인 영역과 구분.
       Streamlit 테마의 secondaryBackgroundColor가 사이드바에 기본 적용되므로 명시적으로 덮어씀 */
    background-color: #fff !important;
    border-right: 1px solid rgba(221, 227, 234, 0.55);
}
/* 사이드바 상단 헤더(숨기기 버튼 행) 왼쪽에 'AI은행원' 제목 삽입 */
[data-testid="stSidebarHeader"] {
    display: flex !important;
    align-items: center !important;
}
[data-testid="stSidebarHeader"]::before {
    content: "AI은행원";
    font-size: 18px;
    font-weight: 700;
    color: var(--blue-dark);
    padding-left: 4px;
    white-space: nowrap;
}
/* 사이드바 상단 여백 축소 → 대화 목록 공간 확보 */
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] {
    padding-top: 0.5rem !important;
}

/* ── 새 채팅: 화이트 카드 + 얇은 테두리, 아이콘은 브랜드 그린 ──────── */
/* st-key-<key> 클래스는 stElementContainer(바깥 래퍼)에 붙고, stButton/버튼은
   그 안쪽 자손이므로 반드시 후손 결합자( )를 쓴다 (직계 자식 > 은 매치 안 됨) */
[data-testid="stSidebar"] .st-key-sidebar_new_chat [data-testid="stButton"] button {
    border: none !important;
    outline: none !important;
    border-radius: 12px !important;
    background: #FFFFFF !important;
    box-shadow: none !important;
    justify-content: flex-start !important;
    min-height: 0 !important;
    padding: 0.6rem 0.9rem !important;
    color: var(--text) !important;
    transition: background 0.15s !important;
}
[data-testid="stSidebar"] .st-key-sidebar_new_chat [data-testid="stButton"] button:hover {
    background: var(--blue-soft) !important;
}
/* 버튼 내부 래퍼(div)가 자체적으로 justify-content:center라 아이콘+텍스트가
   가운데로 몰린다 — 왼쪽 정렬(채팅 검색과 동일한 축)이 되도록 덮어씀 */
[data-testid="stSidebar"] .st-key-sidebar_new_chat [data-testid="stButton"] button > div {
    justify-content: flex-start !important;
}
[data-testid="stSidebar"] .st-key-sidebar_new_chat [data-testid="stButton"] button p {
    font-size: 14px !important;
    font-weight: 600 !important;
}
[data-testid="stSidebar"] .st-key-sidebar_new_chat [data-testid="stButton"] button [data-testid="stIconMaterial"] {
    color: var(--blue-dark) !important;
}

/* ── 채팅 검색: 새 채팅 버튼과 동일한 톤(흰 배경 + 호버 시 blue-soft, radius 12px) ── */
[data-testid="stSidebar"] .st-key-sidebar_search { margin-top: 4px !important; margin-bottom: 0 !important; }
[data-testid="stSidebar"] .st-key-sidebar_search [data-testid="stTextInputRootElement"] {
    border: none !important;
    outline: none !important;
    background: #FFFFFF !important;
    border-radius: 12px !important;
    box-shadow: none !important;
    transition: background 0.15s !important;
    /* 새 채팅 버튼의 아이콘 시작 위치(padding-left 14px)·아이콘-텍스트 간격(8px)과 맞춤 */
    padding-left: 14px !important;
    column-gap: 8px !important;
}
[data-testid="stSidebar"] .st-key-sidebar_search [data-testid="stTextInputRootElement"]:hover {
    background: var(--blue-soft) !important;
}
[data-testid="stSidebar"] .st-key-sidebar_search [data-testid="stTextInputRootElement"]:focus-within {
    background: var(--blue-soft) !important;
    box-shadow: none !important;
}
/* root 안의 이름 없는 래퍼 div 2개(아이콘 감싸는 것 + 입력창 감싸는 것)가
   자체적으로 흰 배경이라 root의 색이 가장자리에만 살짝 비쳐 보였다 — 투명 처리 */
[data-testid="stSidebar"] .st-key-sidebar_search [data-testid="stTextInputRootElement"] > div {
    background: transparent !important;
}
[data-testid="stSidebar"] .st-key-sidebar_search input {
    font-size: 14px !important;
    background: transparent !important;
    color: var(--text) !important;
    /* input 자체의 기본 padding-left(12px)가 아이콘과의 flex gap(8px)에 더해져
       새 채팅 버튼(아이콘-텍스트 8px)보다 훨씬 벌어져 보였다 — 0으로 상쇄 */
    padding-left: 0 !important;
}
[data-testid="stSidebar"] .st-key-sidebar_search [data-testid="stTextInputIcon"] { color: var(--blue-dark) !important; }

/* ── "이전 대화" 소제목: 절제된 스타일 ─────────────────────────────── */
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
    font-size: 11px !important;
    font-weight: 600 !important;
    color: #5F6368 !important;
    letter-spacing: 0.02em !important;
    margin-top: 10px !important;
    margin-bottom: 2px !important;
}

/* 이전 대화 버튼: 한 줄 말줄임 + 컴팩트하게 (더 많이 보이도록) */
[data-testid="stSidebar"] [data-testid="stButton"] > button {
    min-height: 0 !important;
    padding-top: 0.3rem !important;
    padding-bottom: 0.3rem !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] > button p {
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
    font-size: 13.5px !important;
}
/* 대화 목록(제목·삭제): 기본은 투명. columns 내부(stHorizontalBlock)만 타겟 */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stButton"] > button {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    border-radius: 10px !important;
    transition: background 0.15s !important;
}
/* 현재 대화(primary): 연한 그린 필 배경으로 "선택됨"을 명확히 표시
   주의: Streamlit 1.5x부터 버튼 엘리먼트에 kind="primary" 같은 원시 HTML 속성이 안 붙고
   data-testid="stBaseButton-primary"/"stBaseButton-secondary"로만 노출된다(React가 알 수 없는
   DOM 속성인 kind를 렌더링 전에 걸러냄) — button[kind="primary"] 셀렉터는 절대 안 걸리는
   죽은 규칙이었음(2026-08-09 발견, 6곳 전부 동일 문제라 일괄 수정). */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stButton"] button[data-testid="stBaseButton-primary"] {
    background: var(--blue-soft) !important;
    color: var(--blue-dark) !important;
    font-weight: 700 !important;
}
/* hover(선택되지 않은 행): 중립 회색 — 그린은 "선택됨" 전용이라 구분되게 */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stButton"] button[data-testid="stBaseButton-secondary"]:hover {
    background: #F0F2F5 !important;
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
/* 삭제 아이콘: 평소엔 흐리게, 행에 마우스를 올렸을 때만 또렷하게 */
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child
  [data-testid="stButton"] > button {
    opacity: 0.32;
    transition: opacity 0.15s;
}
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"]:hover [data-testid="stColumn"]:last-child
  [data-testid="stButton"] > button {
    opacity: 1;
}
/* 대화 목록 스크롤 컨테이너: 테두리 제거
   (stVerticalBlockBorderWrapper는 이 Streamlit 버전엔 존재하지 않는 testid였음 — 실제로는
   stVerticalBlock. box-shadow 링으로 테두리를 구현하는 경우도 있어 함께 리셋) */
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
    border: none !important;
    box-shadow: none !important;
    outline: none !important;
}
/* 이전 대화 8개 이상일 때(container(height=340, key="conv_list_scroll"))의 스크롤바 —
   기본값은 스크롤 중에만 잠깐 나타나는 macOS 오버레이 스크롤바라 목록에 더 있다는 게
   안 보임. 얇지만 항상 보이는 스크롤바로 강제 표시(Firefox는 scrollbar-width/color,
   Chrome/Safari는 -webkit-scrollbar-*). */
[data-testid="stSidebar"] .st-key-conv_list_scroll {
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
}
[data-testid="stSidebar"] .st-key-conv_list_scroll::-webkit-scrollbar {
    width: 6px;
}
[data-testid="stSidebar"] .st-key-conv_list_scroll::-webkit-scrollbar-track {
    background: transparent;
}
[data-testid="stSidebar"] .st-key-conv_list_scroll::-webkit-scrollbar-thumb {
    background: var(--border);
    border-radius: 3px;
}
[data-testid="stSidebar"] .st-key-conv_list_scroll::-webkit-scrollbar-thumb:hover {
    background: var(--blue-line);
}

/* ── 구분선: 존재감 낮춤 ───────────────────────────────────────────── */
[data-testid="stSidebar"] hr {
    border-color: #E8EAED !important;
    margin: 0.6rem 0 !important;
}

/* ── expander(유의사항): 박스 느낌 제거 + 작은 폰트 + 사이드바 하단 고정 ── */
/* 실제 테두리/배경은 컨테이너가 아니라 summary(헤더)에 있어 둘 다 리셋 */
[data-testid="stSidebar"] [data-testid="stExpander"] {
    border: none !important;
    background: transparent !important;
    box-shadow: none !important;
    outline: none !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {
    border: none !important;
    background: transparent !important;
    box-shadow: none !important;
    outline: none !important;
    padding: 0.35rem 0.2rem !important;
    font-size: 11.5px !important;
    font-weight: 500 !important;
    color: var(--text-sub, #5F6368) !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary svg {
    width: 14px !important;
    height: 14px !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] summary:hover {
    background: var(--blue-soft) !important;
}
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] {
    font-size: 11.5px !important;
    line-height: 1.4 !important;
    padding-top: 0.2rem !important;
    max-height: 200px !important;
    overflow-y: auto !important;
}
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] p,
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] ul,
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] li {
    font-size: 11.5px !important;
}
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] ul {
    padding-left: 1rem !important;
    margin: 0 !important;
}
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] li {
    margin-bottom: 0.3rem !important;
}

/* 사이드바 콘텐츠를 세로 flex로 늘여 '유의사항'을 항상 하단에 고정 */
[data-testid="stSidebarContent"] {
    display: flex !important;
    flex-direction: column !important;
}
[data-testid="stSidebarContent"] [data-testid="stSidebarUserContent"] {
    flex: 1 1 auto !important;
    display: flex !important;
    flex-direction: column !important;
    min-height: 0 !important;
}
[data-testid="stSidebarUserContent"] > div {
    display: flex !important;
    flex-direction: column !important;
    flex: 1 1 auto !important;
    min-height: 0 !important;
}
[data-testid="stSidebarUserContent"] > div > [data-testid="stVerticalBlock"] {
    flex: 1 1 auto !important;
    min-height: 0 !important;
}
/* 유의사항 바로 위 구분선부터 margin-top:auto로 하단에 밀착 */
[data-testid="stSidebarUserContent"] [data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] hr) {
    margin-top: auto !important;
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
    background-color: var(--blue-soft);
    border-radius: 18px 18px 18px 4px;
    padding: 12px 18px;
    /* stVerticalBlock은 기본이 width:100%라 max-width만 주면 문장이 짧아도 항상 72%까지
       늘어나 있었음 — width:fit-content로 내용 길이만큼만 차지하게 하고, max-width는
       긴 문장이 넘칠 때의 상한선으로만 남긴다. */
    width: fit-content;
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
/* 대화 본문 글씨 크기 — Streamlit 기본(14px)이 채팅 내용 치고는 조금 작아 보여서
   살짝 키움(질문/답변 공통). */
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p {
    font-size: 15.5px !important;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [data-testid="stChatMessageAvatarAssistant"] { display: none; }

/* "+" 파일첨부 버튼에 마우스를 올리면 뜨는 허용 파일형식 툴팁 숨김 */
[role="tooltip"] { display: none !important; }

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
.action-btn:hover { border-color: var(--blue); background: var(--blue-soft); color: var(--blue-dark); }
.action-btn svg { width: 14px; height: 14px; pointer-events: none; }
.action-btn.active { color: #0B8457; border-color: #0B8457; background: #E3F6EC; }
/* 숨겨진 retry/좋아요/싫어요 st.button(key가 px_로 시작) — JS .click()으로만 트리거.
   대신 싫어요 폼의 제출/취소 버튼(key가 px_로 시작하지 않음)은 그대로 보여야 한다. */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [class*="st-key-px_"] [data-testid="stButton"] {
    position: absolute !important;
    opacity: 0 !important;
    pointer-events: none !important;
    width: 1px !important; height: 1px !important;
    overflow: hidden !important;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [class*="st-key-px_"] [data-testid="stButton"] button { pointer-events: auto !important; }

/* 싫어요 이유 선택 상자(key=dislike_box_<i>): 체크박스 몇 개뿐인데 채팅 컬럼 전체 폭으로
   퍼져 보이던 것을 내용에 맞는 폭으로 줄임. */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [class*="st-key-dislike_box_"] {
    max-width: 420px;
    /* 기본 padding이 0px 8px(위아래 여백 0)라 글자·버튼이 테두리에 그대로 붙어 답답해
       보였음 — 사방에 여유 있는 여백을 줌(uidesign.tips "Visually Separate Elements": 여백이
       구획을 나누고 인지 부담을 줄인다). */
    padding: 18px 20px !important;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [class*="st-key-dislike_box_"] [data-testid="stVerticalBlock"] {
    gap: 10px;
    /* Streamlit 기본값 max-width:82%가 이 상자 안에서 또 82%로 줄어들어(체크박스/제출·취소
       버튼 줄이 상자 오른쪽 끝까지 못 닿고 여백만 남던 원인) — 상자 폭 안에서는 100% 그대로 채움. */
    max-width: 100% !important;
}
/* 질문("어떤 점이 아쉬웠나요?")과 선택지 텍스트를 다른 색으로 구분하려 했으나(기본값은 둘 다
   --text), 진하기 차이를 두고 갈 때마다("체크박스는 너무 옅고 텍스트는 너무 진하다" →
   "선택지 옅어지니 질문이 상대적으로 옅어 보인다") 반복 피드백을 받아 — 이번엔 아예 질문·
   선택지·체크박스 테두리를 전부 같은 색으로 통일. 새 색상 토큰을 만드는 대신 기존
   --text/--text-sub를 color-mix()로 절반씩 섞은 중간 톤 하나를 --dislike-ink로 선언해
   세 군데(질문/선택지 텍스트/체크박스 테두리)에서 재사용(둘 다 이미 승인된 토큰이라
   임의 색상 추가에 해당하지 않음). */
[class*="st-key-dislike_box_"] {
    --dislike-ink: color-mix(in srgb, var(--text) 50%, var(--text-sub) 50%);
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [class*="st-key-dislike_box_"] [data-testid="stCaptionContainer"] p {
    color: var(--dislike-ink) !important;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [class*="st-key-dislike_box_"] [data-testid="stCheckbox"] label p {
    color: var(--dislike-ink) !important;
}
/* 체크박스 미체크 테두리가 기본값(rgba(60,64,67,.2))이라 너무 옅어 잘 안 보인다는 피드백 —
   미체크 상태(aria-checked="false")만 :has()로 골라 테두리를 또렷하게, 질문·선택지 텍스트와
   같은 색으로 통일. 체크된 상태는 Streamlit 기본(강조색) 그대로 두어 건드리지 않음. */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [class*="st-key-dislike_box_"] [data-testid="stCheckbox"]
  label[data-baseweb="checkbox"]:has(input[aria-checked="false"]) > span:first-child {
    border-color: var(--dislike-ink) !important;
    border-width: 1.5px !important;
}
/* "제출" 버튼에 연한 배경(팝오버 primary 버튼과 동일 레시피)을 넣어 "취소"와 구분되게 함. */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [class*="st-key-dislike_submit_"] [data-testid="stButton"] > button {
    background: var(--blue-soft) !important;
    border-color: var(--blue-line) !important;
    color: var(--blue-dark) !important;
    font-weight: 600 !important;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [class*="st-key-dislike_submit_"] [data-testid="stButton"] > button:hover {
    background: var(--blue-line) !important;
}

/* ── 입력창 ────────────────────────────────────────────────────── */
[data-testid="stChatInput"] { margin-right: 150px !important; }
[data-testid="stChatInput"] > div {
    border-radius: 24px !important;
    border: 1.5px solid var(--blue-line) !important;
    background-color: #FFFFFF !important;
    box-shadow: 0 2px 8px rgba(60,64,67,0.10) !important;
}
[data-testid="stChatInput"] textarea {
    background-color: transparent !important;
    padding-left: 16px !important;
}
[data-testid="stChatInputSubmitButton"][disabled] { color: rgba(49,51,63,0.55) !important; }
[data-testid="stChatInputSubmitButton"]:not([disabled]) {
    color: var(--blue-dark) !important;
    background: var(--blue-soft) !important;
}

/* ── 은행 링크 ─────────────────────────────────────────────────── */
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) a {
    color: var(--blue-dark); text-decoration: none; font-weight: 500;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) a:hover {
    text-decoration: underline;
}

/* ── AI 생각 중 인디케이터 ─────────────────────────────────────────
   기존엔 Streamlit 기본 스피너(회색 링 + 텍스트)를 그대로 써서 나머지 커스텀
   UI 사이에서 유독 눈에 띄었음. 브랜드 톤의 알약형 배지 + 은은한 펄스 애니메이션으로
   교체 — st.spinner() 호출부(app.py 곳곳의 "처리 중"/"검색 중" 등)는 그대로 두고
   testid 기준으로만 재스킨하므로 어떤 spinner 문구든 동일하게 적용된다. */
[data-testid="stSpinner"] {
    display: inline-flex !important;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
    margin: 4px 0;
    background: var(--blue-soft);
    border-radius: 999px;
    width: fit-content;
    animation: aiThinkingPulse 1.6s ease-in-out infinite;
}
[data-testid="stSpinner"] svg {
    width: 16px !important;
    height: 16px !important;
    color: var(--blue) !important;
}
[data-testid="stSpinner"] p {
    color: var(--blue-dark) !important;
    font-size: 14px !important;
    font-weight: 600 !important;
}
@keyframes aiThinkingPulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.6; }
}
@media (prefers-reduced-motion: reduce) {
    [data-testid="stSpinner"] { animation: none; }
}

/* ── AI 응답 대기 커스텀 아이콘 ────────────────────────────────────
   위 stSpinner 재스킨과 별개로, "메시지 전송 → 응답 도착" 구간(에이전트 처리 중 /
   스트리밍 첫 토큰 도착 전)에는 Streamlit 기본 스피너 대신 이 아이콘을 직접 그린다.
   logo-mark.svg의 겹친 두 원을 그대로 축소해 쓴 것 — 새 도형은 없다. 크기는
   고정한 채 두 원이 서로 반대 방향으로 떨어졌다가 로고 모양으로 다시 겹치기를
   반복한다(회전 아님 — 한 번 회전으로 바꿨다가 실제 화면에서 확인해보니 사용자가
   원한 건 이 떨어짐/겹침 동작이라 되돌림). 배경 배지 없이 아이콘+문구를 채팅
   말풍선 배경 위에 그대로 얹는다(Claude "Pondering"/Gemini 점 3개 참고 — 배경
   박스 없는 편이 더 가벼워 보임). st.empty() placeholder에 unsafe_allow_html로
   주입하고, 응답이 도착하면 placeholder를 비운 뒤 실제 내용으로 교체한다
   (헬퍼: _thinking_indicator_html). SVG는 <symbol>+<use> 없이 매번 인라인으로
   그린다 — <use>가 참조하는 그림자 트리 안의 원소에는 CSS 애니메이션이 실제로
   시작되지 않는 걸 확인했다(document.getAnimations()로 검증). */
.mb-thinking {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 4px 0;
    margin: 4px 0;
}
.mb-thinking-svg { display: block; overflow: visible; flex-shrink: 0; }
.mb-thinking-svg .mb-thinking-a { fill: var(--blue); }
.mb-thinking-svg .mb-thinking-b { fill: var(--blue-dark); opacity: 0.68; }
@media (prefers-reduced-motion: no-preference) {
    .mb-thinking-svg .mb-thinking-a { animation: mbThinkingDriftA 2s cubic-bezier(0.4,0,0.2,1) infinite; }
    .mb-thinking-svg .mb-thinking-b { animation: mbThinkingDriftB 2s cubic-bezier(0.4,0,0.2,1) infinite; }
}
@keyframes mbThinkingDriftA { 0%, 100% { transform: translateX(0); } 50% { transform: translateX(-4.9px); } }
@keyframes mbThinkingDriftB { 0%, 100% { transform: translateX(0); } 50% { transform: translateX(4.9px); } }
.mb-thinking-label {
    color: var(--blue-dark);
    font-size: 14px;
    font-weight: 600;
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
    background-color: var(--blue-soft) !important;
    border: 1.5px solid var(--blue-line) !important;
    border-radius: 24px !important;
    padding: 0 16px !important;
    font-size: 0.8rem !important; font-weight: 500 !important;
    color: var(--blue-dark) !important;
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
[data-testid="stPopoverBody"] [data-testid="stButton"] button[data-testid="stBaseButton-secondary"] {
    border: none !important; background: transparent !important; color: var(--text) !important;
}
[data-testid="stPopoverBody"] [data-testid="stButton"] button[data-testid="stBaseButton-secondary"]:hover {
    background: var(--bg-soft) !important; color: var(--blue-dark) !important;
}
[data-testid="stPopoverBody"] [data-testid="stButton"] button[data-testid="stBaseButton-primary"] {
    border: none !important; background: var(--blue-soft) !important;
    color: var(--blue-dark) !important; font-weight: 600 !important;
}
[data-testid="stPopoverBody"] [data-testid="stButton"] button[data-testid="stBaseButton-primary"]:hover {
    background: var(--blue-line) !important; color: var(--blue-dark) !important;
}
/* 추천 질문(제안) 버튼: 가로 스크롤 리본의 작은 알약형 칩.
   st-key-<key> 클래스는 stElementContainer(바깥 래퍼)에 붙고 button은 stButton 안쪽
   자손이므로 반드시 후손 결합자를 쓴다(직계 자식 > 은 매치 안 됨 — 실제로 button의 부모는
   stButton div이고, st-key-sugg_ 클래스는 그 바깥의 stElementContainer에 있음).
   배경/호버는 흰 바탕 + 옅은 테두리 + 호버 시 그림자 확대·상승(카드형 톤), 모양만 알약형 유지. */
[class*="st-key-sugg_"] { width: fit-content !important; flex-shrink: 0 !important; }
[class*="st-key-sugg_"] button {
    width: fit-content !important;
    padding: 7px 14px !important;
    border-radius: 18px !important;
    border: 1px solid var(--border) !important;
    background: #FFFFFF !important;
    color: var(--text) !important;
    font-weight: 500 !important;
    font-size: 13px !important;
    box-shadow: 0 1px 3px rgba(60, 64, 67, 0.06) !important;
    transition: box-shadow 0.15s ease, transform 0.15s ease, border-color 0.15s ease !important;
    white-space: nowrap !important;
}
[class*="st-key-sugg_"] button:hover {
    border-color: var(--blue-line) !important;
    box-shadow: 0 6px 16px rgba(15, 169, 104, 0.18) !important;
    transform: translateY(-2px) !important;
}
[class*="st-key-sugg_"] button:active {
    transform: translateY(0) scale(0.98) !important;
}
[class*="st-key-sugg_"] button p { font-size: 13px !important; }
[class*="st-key-sugg_"] button > div { width: auto !important; }
/* 빈 화면(새 채팅) 전용 레이아웃 — 원래 greeting 렌더 블록 안에 있었는데, 그 블록은
   _pending_input(제출 직후)엔 미렌더되어 여기 CSS가 통째로 DOM에서 사라졌었다.
   그 결과 제출 순간 추천 칩을 숨기던 규칙(바로 아래 body.kw-open 규칙)까지 같이 사라져,
   Streamlit이 칩 버튼 자체를 지우기 전 한 프레임 동안 기본값(display:flex)으로 칩이
   잠깐 다시 보이는 "잔상"이 실측 확인됐다(제출 직후 body 클래스가 빈 문자열로 리셋되는
   순간과 겹침). 그래서 대화 상태와 무관하게 항상 떠 있어야 하는 이 CSS를 전역 블록으로 옮김. */
body.chat-empty [data-testid="stBottom"]{
  transform: translateY(-41vh) !important;   /* 레이아웃(사이드바 오프셋·가로중심) 유지한 채 위로만 */
  background: transparent !important; box-shadow: none !important; border: none !important;
}
[data-testid="stChatInput"]{ margin-right: 0 !important; }
body.chat-empty [data-testid="stChatInput"]{
  max-width: 520px !important; margin-left: auto !important; margin-right: auto !important;
}
.stMain{ position: relative !important; }
[data-testid="stHorizontalBlock"]:has([class*="st-key-sugg_"]){
  position: absolute !important; left: 0 !important; right: 0 !important;
  top: 60% !important;
  justify-content: center !important; gap: 10px !important;
  display: none !important; z-index: 39;
  flex-wrap: nowrap !important;
  overflow-x: auto !important;
  overflow-y: hidden !important;
  padding: 4px 24px 10px !important;
  scrollbar-width: none !important;      /* Firefox: 스크롤바 숨김 */
  -ms-overflow-style: none !important;   /* 구형 Edge: 스크롤바 숨김 */
}
[data-testid="stHorizontalBlock"]:has([class*="st-key-sugg_"])::-webkit-scrollbar{
  display: none !important;
}
[data-testid="stHorizontalBlock"]:has([class*="st-key-sugg_"]) > [data-testid="stColumn"]{
  flex: 0 0 auto !important; width: auto !important; min-width: 0 !important;
}
body.kw-open [data-testid="stHorizontalBlock"]:has([class*="st-key-sugg_"]){ display: flex !important; }
/* 제출 시작 즉시 인사말·키워드 숨김(블로킹 처리 중 잔상 방지) + 입력창 하단 고정 */
body.chat-submitting .chat-hero-greeting,
body.chat-submitting [data-testid="stHorizontalBlock"]:has([class*="st-key-sugg_"]){ display: none !important; }
body.chat-submitting [data-testid="stBottom"]{ transform: none !important; }
/* 이체 확인 카드의 "이체하기"/"취소" 버튼 간격. 컬럼 자체를 content-width로 줄이려던
   첫 시도(width:auto+min-width:0)는 Streamlit 내부 width:100% 중첩 구조와 충돌해 버튼이
   쪼그라들며 "이체/하기"처럼 줄바꿈되는 버그를 냈다(2026-08-10) — 대신 버튼 쪽에
   use_container_width=True를 줘서 버튼이 컬럼 폭을 정확히 채우게 했고(app.py 코드),
   그 덕분에 여기 gap 값이 곧 버튼 사이 실제 간격이 된다. */
[data-testid="stHorizontalBlock"]:has(.st-key-tf_exec){ gap: 16px !important; }
</style>""", unsafe_allow_html=True)

def _thinking_indicator_html(label: str = "답변을 준비하고 있어요…") -> str:
    """AI 응답 대기 중 채팅창에 표시할 커스텀 아이콘 HTML.
    st.empty() placeholder에 markdown(..., unsafe_allow_html=True)로 주입해 쓴다.

    <symbol>+<use> 참조 방식으로 만들었다가, <use>가 인스턴스화하는 그림자 트리 안의
    <g>에는 CSS @keyframes 애니메이션이 실제로 시작되지 않는 걸 확인했다
    (document.getAnimations()로 검증 — 애니메이션이 전혀 잡히지 않음).
    그래서 매번 원 두 개를 직접 인라인으로 그린다. 이 아이콘은 한 번에 한 곳에서만
    쓰이므로 sprite로 공유할 이유도 없다. 두 원은 각자 반대 방향으로 translateX
    되며 떨어졌다 로고 모양으로 다시 겹치기를 반복한다(회전 아님)."""
    return (
        '<div class="mb-thinking">'
        '<svg class="mb-thinking-svg" width="18" height="18" viewBox="0 0 40 40" '
        'role="img" aria-label="응답 대기 중">'
        '<circle class="mb-thinking-a" cx="16" cy="20" r="8"/>'
        '<circle class="mb-thinking-b" cx="24" cy="20" r="8"/>'
        '</svg>'
        f'<span class="mb-thinking-label">{label}</span>'
        '</div>'
    )

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
# 상단 'AI은행원' 제목은 CSS(stSidebarHeader::before)로 숨기기 버튼 행에 표시된다.
with st.sidebar:
    if st.button("새 채팅", icon=":material/edit_square:", use_container_width=True,
                 key="sidebar_new_chat"):
        storage.save_conversation(st.session_state.conversation)
        st.session_state.conversation = storage.new_conversation()
        st.rerun()

    st.text_input(
        "채팅 검색", icon=":material/search:", placeholder="채팅 검색",
        label_visibility="collapsed", key="sidebar_search",
    )

    st.caption("이전 대화")
    current_id = st.session_state.conversation["id"]
    _query = (st.session_state.get("sidebar_search") or "").strip()
    _convos = storage.list_conversations(query=_query)
    # 대화가 많으면 독립 스크롤 영역으로 → 목록이 늘어나도 더 많이 탐색 가능.
    # (적을 때는 자연 높이로 두어 빈 상자가 생기지 않게 함)
    if _query and not _convos:
        st.caption("검색 결과가 없습니다")
    _list_box = (
        st.container(height=340, key="conv_list_scroll") if len(_convos) > 7 else st.container()
    )
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

    # 파일 첨부는 입력창의 "+" 버튼(st.chat_input accept_file)으로 이동했다.

    st.divider()
    with st.expander("AI은행원 유의사항", icon=":material/info:", expanded=False):
        # 이체 한도 문구는 관리자가 저장한 이체 정책(config.json)을 그대로 반영한다.
        _pol = config.load()

        def _limit_txt(n: int) -> str:
            n = int(n)
            eok, man = n // 100_000_000, (n % 100_000_000) // 10_000
            if eok and man:
                return f"{eok}억 {man:,}만원"
            if eok:
                return f"{eok}억원"
            if n % 10_000 == 0:
                return f"{n // 10_000:,}만원"
            return f"{n:,}원"

        _once_txt = _limit_txt(_pol.get("transfer_limit", 5_000_000))
        _daily_txt = _limit_txt(_pol.get("daily_transfer_limit", 10_000_000))
        st.markdown(
            "- 모든 답변은 AI가 생성하며, 정확하지 않을 수 있습니다.\n"
            "- 같은 질문도 대화 맥락에 따라 답변이 달라질 수 있습니다.\n"
            "- 입력창에 비밀번호·주민등록번호 등 민감한 개인정보를 입력하지 마세요.\n"
            "- **AI 이체**는 예금주·금액을 확인하고 비밀번호로 승인해야만 실행됩니다. 승인 없이 자동으로 이체되지 않습니다.\n"
            f"- **이체 한도(간편이체 기준): 1회 {_once_txt} / 1일 {_daily_txt}**이며, 안전한 금융거래를 위해 일부 계좌로의 이체가 제한될 수 있습니다.\n"
            "- **예약 이체**는 지정한 시각에 실행되며, **지연 이체**는 실행 전까지 취소할 수 있습니다.\n"
            "- 이체 관련 사고·오류는 전자금융거래법 및 매치뱅크 약관에 따라 처리되며, 이용자의 고의 또는 중대한 과실이 있는 경우 배상이 제한될 수 있습니다. (본 서비스는 데모입니다.)\n"
            "- 상품 정보는 참고용이며 투자·금융 자문이 아닙니다."
        )


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

# 입력창은 항상 하단 고정 위젯 — 먼저 호출해 '제출 여부'를 조기에 판단한다.
# (제출 직후 run에서는 블로킹 처리 동안 메시지 버블이 늦게 그려지므로, 빈화면 레이아웃을
#  건너뛰어야 입력창이 위로 떴다가 내려오는 깜빡임이 없다.)
_chat_input = st.chat_input(
    "메시지를 입력하세요…",
    accept_file="multiple",
    file_type=["txt", "md", "py", "js", "ts", "csv", "json", "html", "css",
               "pdf", "png", "jpg", "jpeg", "gif", "webp"],
)
_pending_input = bool(_chat_input or st.session_state.get("_retry_prompt")
                      or st.session_state.get("_chip_prompt"))

if not conv["messages"] and not _pending_input:
    import html as _html
    _name = st.session_state.get("auth_name")
    _greet = (f"{_html.escape(_name)}님 안녕하세요, 무엇을 도와드릴까요?"
              if _name else "안녕하세요, 무엇을 도와드릴까요?")
    # 빈 화면 전용 레이아웃: 입력창을 인사말 바로 아래 중앙으로 끌어올리고,
    # 추천 키워드는 기본 숨김 → 입력창을 누르면(포커스) 노출. (대화 시작 후엔 이 블록 미렌더 → 입력창 하단 복귀)
    # 관련 CSS는 여기가 아니라 최상단 전역 <style> 블록에 있다 — 이 블록 전체가
    # _pending_input(제출 직후)일 땐 미렌더되는데, 이 CSS를 여기 두면 제출 순간 스타일시트 자체가
    # DOM에서 사라져 추천 칩이 기본값(display:flex)으로 잠깐 다시 보이는 "잔상" 버그가 있었다
    # (실측 확인: 제출 직후 body 클래스가 비워지는 한 프레임에서 칩이 flex로 노출됨).
    # 그래서 대화 상태와 무관하게 항상 존재해야 하는 CSS라 전역 블록으로 옮겼다.
    st.markdown("<div style='height:16vh'></div>", unsafe_allow_html=True)

    if not auth_token:
        st.markdown("""
<div class='chat-hero-greeting' style='text-align:center; padding:1.5rem 2rem;'>
  <p style='font-size:2rem; font-weight:400; color:#3C4043;
            letter-spacing:-0.3px; line-height:1.35; margin:0;'>
    로그인하고 AI은행원과 대화를 나눠보세요.
  </p>
</div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""
<div class='chat-hero-greeting' style='text-align:center; padding:1.5rem 2rem;'>
  <p style='font-size:2rem; font-weight:400; color:#3C4043;
            letter-spacing:-0.3px; line-height:1.35; margin:0;'>
    {_greet}
  </p>
</div>""", unsafe_allow_html=True)

        # 대화 제안(추천 프롬프트) — 클릭 시 해당 질문 전송 (로그인 후에만 노출)
        _SUGGESTIONS = [
            "💰 내 계좌 잔액 알려줘",
            "📄 최근 거래내역 보여줘",
            "📈 금리 높은 정기예금 추천해줘",
            "🐷 적금 상품 비교해줘",
            "💸 오늘 이체 한도 얼마 남았어?",
            "🙋 공지사항 알려줘",
        ]
        # 문장 길이에 맞는 칩 형태: 한 줄에 나열해 가로 스크롤 리본으로(컨테이너 폭 안 채움)
        _scols = st.columns(len(_SUGGESTIONS))
        for _i, _sugg in enumerate(_SUGGESTIONS):
            if _scols[_i].button(_sugg, key=f"sugg_{_i}", use_container_width=False):
                # _retry_prompt와 별개 키를 쓴다 — _retry_prompt는 "이미 conv에 있는 메시지를
                # 다시 보낸다"는 의미라 재추가를 건너뛰는데, 칩은 이번이 처음 보내는 새 메시지라
                # conv에 추가돼야 사이드바 제목(첫 사용자 메시지 기준)이 "새 대화"로 안 남는다.
                st.session_state["_chip_prompt"] = _sugg.split(" ", 1)[1]  # 이모지 제거
                st.rerun()

    # 입력창을 누르면(포커스) 추천 키워드 노출 (iframe → 부모 DOM)
    _components.html("""<script>
    (function(){
      var pd = window.parent.document;
      pd.body.classList.add('chat-empty');       // 빈 화면 레이아웃 즉시 적용(로드 깜빡임 방지)
      pd.body.classList.remove('kw-open');       // 새 채팅 시작 시 키워드는 다시 숨김(포커스하면 노출)
      pd.body.classList.remove('chat-submitting'); // 새 채팅이면 제출상태 해제(인사말·키워드 다시 표시)
      function bind(){
        var ta = pd.querySelector('[data-testid="stChatInput"] textarea');
        // "한 번만 바인딩"(dataset 플래그) 방식은, 이 스크립트가 담긴 iframe이 로드되기 전에
        // 사용자가 먼저 입력창을 클릭하거나(포커스 이벤트를 놓침) 새 채팅/새로고침 후 textarea가
        // 교체되는 타이밍과 겹치면 리스너가 아예 안 붙는 경우가 있었다 — named 함수 + remove 후
        // re-add로 매번 다시 걸어도 중복 등록이 안 되게 해서(멱등), 언제 바인딩을 시도하든 항상
        // 최신 textarea에 리스너가 붙어있도록 한다.
        if (ta){
          ta.removeEventListener('focus', kwFocusHandler);
          ta.addEventListener('focus', kwFocusHandler);
        }
      }
      function kwFocusHandler(){ pd.body.classList.add('kw-open'); }
      bind();
      new MutationObserver(bind).observe(pd.body, {childList:true, subtree:true});
    })();
    </script>""", height=0)
    st.markdown("<div style='height:4vh'></div>", unsafe_allow_html=True)

_DISLIKE_REASONS = ["부정확한 정보", "원하는 답변이 아님", "응답이 느림/오류", "말투/스타일이 아쉬움", "안전/법적 우려", "기타"]


def _submit_chat_feedback(conv: dict, msg: dict, msg_index: int, rating: str,
                           reasons: list[str] | None = None, comment: str = "") -> None:
    """평가를 백엔드(bank.db, Backoffice 집계용)에 저장 + 대화 JSON에도 남겨(재실행해도 버튼 강조 유지)."""
    question = ""
    if msg_index > 0 and conv["messages"][msg_index - 1]["role"] == "user":
        question = conv["messages"][msg_index - 1]["content"]
    try:
        requests.post(f"{_BACKEND_URL}/api/chat-feedback", json={
            "conversation_id": conv["id"],
            "message_index": msg_index,
            "rating": rating,
            "reasons": reasons or [],
            "comment": comment,
            "question": question,
            "answer": msg["content"],
        }, timeout=3)
    except Exception:
        pass  # 백엔드가 꺼져 있어도 채팅 자체는 계속 동작해야 함
    msg["feedback"] = {"rating": rating, "reasons": reasons or [], "comment": comment}
    storage.save_conversation(conv)


for i, msg in enumerate(conv["messages"]):
    with st.chat_message(msg["role"]):
        _display = _inject_bank_links(msg["content"]) if msg["role"] == "assistant" else msg["content"]
        st.markdown(_display)

        if msg["role"] == "assistant" and msg.get("type") != "transfer_record":
            _cb64 = _base64.b64encode(msg["content"].encode()).decode()
            _fb = msg.get("feedback") or {}
            _like_cls = " active" if _fb.get("rating") == "up" else ""
            _dislike_cls = " active" if _fb.get("rating") == "down" else ""
            st.markdown(f"""
<div class='msg-actions'>
  <button class='action-btn{_like_cls}' title='좋아요' data-idx="{i}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/><path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
  </button>
  <button class='action-btn{_dislike_cls}' title='싫어요' data-idx="{i}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z"/><path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg>
  </button>
  <button class='action-btn' title='다시 시도' data-idx="{i}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-5"/></svg>
  </button>
  <button class='action-btn' title='복사' data-b64="{_cb64}">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
  </button>
</div>""", unsafe_allow_html=True)
            if st.button("↺", key=f"px_retry_{i}"):
                conv["messages"] = conv["messages"][:i]
                storage.save_conversation(conv)
                if conv["messages"] and conv["messages"][-1]["role"] == "user":
                    st.session_state["_retry_prompt"] = conv["messages"][-1]["content"]
                st.rerun()
            if st.button("👍", key=f"px_like_{i}"):
                st.session_state.pop(f"show_dislike_form_{i}", None)
                _submit_chat_feedback(conv, msg, i, "up")
                st.rerun()
            if st.button("👎", key=f"px_dislike_{i}"):
                st.session_state[f"show_dislike_form_{i}"] = True
                st.rerun()

            if st.session_state.get(f"show_dislike_form_{i}"):
                with st.container(border=True, key=f"dislike_box_{i}"):
                    st.caption("어떤 점이 아쉬웠나요? (선택)")
                    _selected = []
                    for ridx, r in enumerate(_DISLIKE_REASONS):
                        if st.checkbox(r, key=f"reason_{i}_{ridx}"):
                            _selected.append(r)
                    _etc_text = ""
                    if "기타" in _selected:
                        _etc_text = st.text_input("기타 사유를 입력해주세요", key=f"etc_{i}")
                    _fc1, _fc2 = st.columns(2)
                    _canceled = _fc1.button("취소", key=f"dislike_cancel_{i}", use_container_width=True)
                    _submitted = _fc2.button("제출", key=f"dislike_submit_{i}", use_container_width=True)
                if _submitted:
                    _submit_chat_feedback(conv, msg, i, "down", reasons=_selected, comment=_etc_text)
                if _submitted or _canceled:
                    st.session_state.pop(f"show_dislike_form_{i}", None)
                    for ridx in range(len(_DISLIKE_REASONS)):
                        st.session_state.pop(f"reason_{i}_{ridx}", None)
                    st.session_state.pop(f"etc_{i}", None)
                    st.rerun()

# ── 이체 확인 카드 헬퍼 ──────────────────────────────────────────────
def _won_kor(n: int) -> str:
    """금액을 억/만 단위 한글 병기용 문자열로. 예: 20000 → '2만원'."""
    n = int(n)
    eok, rest = divmod(n, 100_000_000)
    man, won = divmod(rest, 10_000)
    parts = []
    if eok:
        parts.append(f"{eok}억")
    if man:
        parts.append(f"{man}만")
    if won:
        parts.append(f"{won}")
    return ("".join(parts) or "0") + "원"


def _my_accounts(token: str) -> list[dict]:
    try:
        r = requests.get(f"{_BACKEND_URL}/api/accounts",
                         headers={"Authorization": f"Bearer {token}"}, timeout=3)
        if r.ok:
            return r.json().get("accounts", [])
    except Exception:
        pass
    return []


# ── 이체 진행상태 폴링 헬퍼 ────────────────────────────────────────────
def _poll_transfer_status(transfer_id: int, token: str,
                          timeout_s: float = 6.0, interval_s: float = 0.3) -> dict:
    """Kafka 비동기 처리 중(pending)인 이체가 completed/failed로 바뀔 때까지 짧게 폴링한다.
    타임아웃까지 pending이면 마지막 응답을 그대로 반환(호출부가 지연 안내 문구로 처리)."""
    deadline = time.time() + timeout_s
    tr = {"status": "pending"}
    while time.time() < deadline:
        try:
            r = requests.get(f"{_BACKEND_URL}/api/transfers/{transfer_id}",
                             headers={"Authorization": f"Bearer {token}"}, timeout=3)
            if r.ok:
                tr = r.json()
                if tr.get("status") != "pending":
                    return tr
        except Exception:
            pass
        time.sleep(interval_s)
    return tr


def _clear_transfer_widget_keys() -> None:
    """이체 플로우 위젯 상태를 정리 — 다음 번 새 이체에 이전 값(특히 비밀번호)이 새지 않도록."""
    for _k in ("tf_from_sel", "tf_when", "tf_delay", "tf_sched_d", "tf_sched_t",
               "tf_confirm_chk", "tf_pw"):
        st.session_state.pop(_k, None)


# ── 에이전트 이체 확인 카드 (사용자 확인 + 비밀번호 인증 후에만 실행) ──
# 예금주·시점 선택·비밀번호를 한 화면에서 받고, "이체하기" 한 번으로 바로 실행한다
# (중간에 "다음" 단계를 두는 2단계 플로우로 바꿨다가 사용자 요청으로 원래대로 되돌림 — 2026-08-10).
_pending = st.session_state.get("pending_transfer")
if _pending:
    _accts = _my_accounts(auth_token)
    _amt = _pending["amount"]
    with st.chat_message("assistant"):
        st.markdown(f"**💸 {_pending['holder_name']}님에게 {_amt:,}원({_won_kor(_amt)}) 이체할까요?**")

        # 출금 계좌 선택(계좌 2개 이상일 때)
        _from = _pending["from_account"]
        if len(_accts) > 1:
            _opts = [a["account_no"] for a in _accts]
            _labels = {a["account_no"]: f"{a['bank_name']} {a['account_no']} (잔액 {a['balance']:,}원)"
                       for a in _accts}
            if _from in _opts:
                _def = _opts.index(_from)
            else:
                _def = next((i for i, a in enumerate(_accts) if a.get("is_primary")), 0)
            _from = st.selectbox("출금 계좌", _opts, index=_def,
                                 format_func=lambda x: _labels.get(x, x), key="tf_from_sel")

        _from_bank = next((a["bank_name"] for a in _accts if a["account_no"] == _from), "")
        st.markdown(
            f"- **출금계좌**: {_from_bank} {_from}\n"
            f"- **받는계좌**: {_pending['bank_name']} {_pending['to_account']} · 예금주 **{_pending['holder_name']}**\n"
            f"- 수수료: {_pending['fee']:,}원 (참고 · 실제 수수료는 처리 시 확정)"
        )
        if _pending.get("is_new_payee"):
            st.warning("⚠️ 처음 보내는 계좌입니다. 예금주명을 꼭 확인하세요.")
        st.caption("AI가 이체를 위해 정리한 정보입니다. 정확한지 확인 후 진행해 주세요.")

        # 실행 시점 선택: 즉시 / 지연(취소 가능) / 예약(지정 시각)
        _when = st.radio("이체 시점", ["즉시 이체", "지연 이체 (취소 가능)", "예약 이체 (지정 시각)"],
                         horizontal=True, key="tf_when")
        _sched_at = None
        _delay_min = 0
        if _when == "지연 이체 (취소 가능)":
            _delay_labels = {"10분 후": 10, "30분 후": 30, "1시간 후": 60}
            _dsel = st.selectbox("지연 시간", list(_delay_labels), key="tf_delay")
            _delay_min = _delay_labels[_dsel]
            st.caption(f"⏳ {_dsel} 실행됩니다. 실행 전까지 내 계좌·관리자 화면에서 취소할 수 있어요.")
        elif _when == "예약 이체 (지정 시각)":
            import datetime as _dt
            _now = _dt.datetime.now()
            _cd, _ct = st.columns(2)
            _pd_date = _cd.date_input("예약 날짜", value=_now.date(),
                                      min_value=_now.date(), key="tf_sched_d")
            _pd_time = _ct.time_input("예약 시각", value=(_now + _dt.timedelta(hours=1)).time(),
                                      key="tf_sched_t")
            _sched_dt = _dt.datetime.combine(_pd_date, _pd_time)
            _sched_at = _sched_dt.timestamp()
            if _sched_at <= _now.timestamp() + 30:
                st.warning("예약 시각은 현재보다 미래여야 합니다.")
            else:
                st.caption(f"🗓️ {_sched_dt.strftime('%Y-%m-%d %H:%M')}에 실행 예약됩니다.")

        _ok = st.checkbox("받는 분(예금주명)과 금액을 확인했습니다", key="tf_confirm_chk")
        _pw = st.text_input("이체 비밀번호 (숫자 6자리)", type="password", key="tf_pw",
                            max_chars=6, placeholder="이체 비밀번호 6자리를 입력하세요")

        # 이체하기/취소 버튼을 화면 양끝으로 벌리지 않고 나란히 붙여 배치한다
        # (동일폭 st.columns(2)는 두 버튼을 컨테이너 좌우 끝으로 밀어놓는 문제가 있었음).
        # use_container_width=True로 버튼이 컬럼 폭을 정확히 채우게 해서, 버튼 사이 실제
        # 간격이 gap 값 그대로 나오게 한다(컬럼이 버튼보다 넓어서 남는 여백 때문에 간격이
        # 벌어져 보이던 문제를 CSS 폭 트릭 없이 해결 — 그 트릭이 버튼 줄바꿈 버그를 냈었음).
        _c1, _c2, _ = st.columns([1, 1, 3], gap="small")
        if _c1.button("이체하기", type="primary", key="tf_exec", use_container_width=True):
            if not _ok:
                st.warning("예금주명과 금액을 확인한 뒤 체크해 주세요.")
            elif not _pw:
                st.warning("이체 비밀번호를 입력해 주세요.")
            elif _when == "예약 이체 (지정 시각)" and (not _sched_at or _sched_at <= time.time() + 30):
                st.warning("예약 시각을 현재보다 미래로 설정해 주세요.")
            else:
                _thinking_ph = st.empty()
                _thinking_ph.markdown(_thinking_indicator_html("이체를 처리하고 있어요…"),
                                      unsafe_allow_html=True)
                _exec = dict(_pending, from_account=_from)
                _res = agent.execute_transfer(_exec, auth_token, _pw,
                                              scheduled_at=_sched_at, delay_minutes=_delay_min)
                if "error" in _res:
                    _thinking_ph.empty()
                    st.error(f"이체 실패: {_res['error']}")   # 카드 유지 → 수정 후 재시도
                else:
                    _status = _res.get("status")
                    if _status == "scheduled":
                        import datetime as _dt2
                        _when_txt = _dt2.datetime.fromtimestamp(_res["scheduled_at"]).strftime("%Y-%m-%d %H:%M")
                        _final_text = (
                            f"🗓️ **예약 완료** — {_pending['holder_name']}님에게 {_amt:,}원을 "
                            f"{_when_txt}에 이체하도록 예약했어요. (거래번호 {_res['transfer_id']}) "
                            f"실행 전까지 취소할 수 있어요.")
                    elif _status == "delayed":
                        import datetime as _dt2
                        _when_txt = _dt2.datetime.fromtimestamp(_res["scheduled_at"]).strftime("%H:%M")
                        _final_text = (
                            f"⏳ **지연 이체 접수** — {_pending['holder_name']}님에게 {_amt:,}원을 "
                            f"{_when_txt}에 이체합니다. (거래번호 {_res['transfer_id']}) "
                            f"그 전까지 내 계좌·관리자 화면에서 취소할 수 있어요.")
                    elif _status == "pending":
                        # Kafka 비동기 처리 — 워커가 completed/failed로 바꿀 때까지 짧게 폴링
                        _tr = _poll_transfer_status(_res["transfer_id"], auth_token)
                        _final_status = _tr.get("status")
                        if _final_status == "completed":
                            _bal = next((a["balance"] for a in _my_accounts(auth_token)
                                         if a["account_no"] == _from), None)
                            _tail = f" · 출금계좌({_from[-4:]}) 잔액 {_bal:,}원" if _bal is not None else ""
                            _final_text = f"✅ **이체 완료** — {_pending['holder_name']}님에게 {_amt:,}원을 보냈어요.{_tail}"
                            st.session_state["_show_txn_link"] = {"account_no": _from}
                        elif _final_status == "failed":
                            _reason = _tr.get("error") or "알 수 없는 오류"
                            _final_text = f"❌ **이체 실패** — {_reason}"
                        else:
                            _final_text = ("⏳ 처리가 지연되고 있어요. 완료되면 내 계좌 거래내역에서 "
                                          f"확인해 주세요. (거래번호 {_res['transfer_id']})")
                    else:  # "completed" (Kafka 꺼진 동기 경로 — 이미 처리 끝난 상태)
                        _bal = next((a["balance"] for a in _my_accounts(auth_token)
                                     if a["account_no"] == _from), None)
                        _tail = f" · 출금계좌({_from[-4:]}) 잔액 {_bal:,}원" if _bal is not None else ""
                        _final_text = f"✅ **이체 완료** — {_pending['holder_name']}님에게 {_amt:,}원을 보냈어요.{_tail}"
                        st.session_state["_show_txn_link"] = {"account_no": _from}

                    _thinking_ph.empty()
                    conv["messages"].append({"role": "assistant", "content": _final_text})
                    st.session_state.pop("pending_transfer", None)
                    _clear_transfer_widget_keys()
                    storage.save_conversation(conv)
                    st.rerun()
        if _c2.button("취소", key="tf_cancel_confirm", use_container_width=True):
            st.session_state.pop("pending_transfer", None)
            _clear_transfer_widget_keys()
            conv["messages"].append({"role": "assistant", "content": "이체를 취소했습니다."})
            storage.save_conversation(conv)
            st.rerun()

# ── 이체 완료 후: 내 계좌 거래내역으로 이동하는 액션(부모 SPA에 postMessage) ──
_txn_link = st.session_state.get("_show_txn_link")
if _txn_link:
    _components.html(f"""
    <div style="padding:2px 0 8px">
      <button onclick="window.parent.postMessage({{type:'goto-account', account_no:'{_txn_link['account_no']}'}}, '*')"
        style="border:1px solid #A8E0C4;background:#E3F6EC;color:#0B8457;border-radius:20px;
               padding:8px 16px;font-size:14px;font-weight:600;cursor:pointer;">
        📄 이체내역 조회하기
      </button>
    </div>""", height=52)

# ── 액션 버튼 이벤트 핸들러 주입 (iframe → 부모 DOM) ────────────────
_components.html("""<script>
(function(){
  // 예약이체 달력(BaseWeb Datepicker) 헤더/요일을 한글로 치환.
  // 텍스트 내용 기준 매칭 → 클래스명 변화에 견고. 한글 텍스트는 매칭 안 돼 재치환 루프 없음.
  var MONTHS={January:1,February:2,March:3,April:4,May:5,June:6,
              July:7,August:8,September:9,October:10,November:11,December:12};
  var WD={Sunday:'일',Monday:'월',Tuesday:'화',Wednesday:'수',Thursday:'목',Friday:'금',Saturday:'토',
          Sun:'일',Mon:'월',Tue:'화',Wed:'수',Thu:'목',Fri:'금',Sat:'토',
          Su:'일',Mo:'월',Tu:'화',We:'수',Th:'목',Fr:'금',Sa:'토'};
  function localizeCalendar(pd){
    var cals=pd.querySelectorAll('[data-baseweb="calendar"]');
    Array.prototype.forEach.call(cals,function(cal){
      // 텍스트 노드 직접 치환(월 버튼은 텍스트+화살표 혼합이라 leaf가 아님).
      var w=pd.createTreeWalker(cal,NodeFilter.SHOW_TEXT,null),n,nodes=[];
      while(n=w.nextNode())nodes.push(n);
      nodes.forEach(function(tn){
        var t=(tn.nodeValue||'').trim();
        if(!t)return;
        var m=t.match(/^([A-Z][a-z]+)\\s+(\\d{4})$/);       // 혹시 "July 2026" 합쳐진 경우
        if(m&&MONTHS[m[1]]){ tn.nodeValue=m[2]+'년 '+MONTHS[m[1]]+'월'; return; }
        if(MONTHS[t]){ tn.nodeValue=MONTHS[t]+'월'; return; }        // "July"->"7월"
        if(/^\\d{4}$/.test(t)){ tn.nodeValue=t+'년'; return; }        // "2026"->"2026년"
        if(WD[t]){ tn.nodeValue=WD[t]; }                             // 요일(영문일 때만)
      });
      // 헤더 버튼 순서를 연도->월(한국식)로 정렬. 이미 정렬됐으면 skip(루프 방지).
      var mb=null,yb=null;
      Array.prototype.forEach.call(cal.querySelectorAll('button'),function(b){
        var t=(b.textContent||'').trim();
        if(/^\\d{1,2}월$/.test(t))mb=b;
        else if(/^\\d{4}년$/.test(t))yb=b;
      });
      if(mb&&yb&&mb.parentElement===yb.parentElement){
        var kids=Array.prototype.slice.call(mb.parentElement.children);
        if(kids.indexOf(yb)>kids.indexOf(mb))mb.parentElement.insertBefore(yb,mb);
      }
    });
    // 월/연 드롭다운 팝오버 옵션도 한글화(캘린더 밖 portal). 연도는 '모든 옵션이 4자리'일 때만.
    Array.prototype.forEach.call(pd.querySelectorAll('ul[data-baseweb=\"menu\"]'),function(menu){
      var items=menu.querySelectorAll('[role=\"option\"]');
      if(!items.length)return;
      var allYears=Array.prototype.every.call(items,function(li){return /^\\d{4}$/.test((li.textContent||'').trim());});
      Array.prototype.forEach.call(items,function(li){
        var t=(li.textContent||'').trim();
        if(MONTHS[t])li.textContent=MONTHS[t]+'월';
        else if(allYears&&/^\\d{4}$/.test(t))li.textContent=t+'년';
      });
    });
  }
  function attach(){
    var pd=window.parent.document;
    // 빈 화면(메시지 0개)에서만 입력창을 위로 올리는 레이아웃 적용 → 대화 시작 즉시 하단 복귀
    var _msgN = pd.querySelectorAll('[data-testid="stChatMessage"]').length;
    pd.body.classList.toggle('chat-empty', _msgN === 0);
    // 대화가 확정되면(메시지 존재) 제출/키워드 상태를 정리 → 다음 빈 화면이 깨끗하게 시작
    if (_msgN > 0){ pd.body.classList.remove('chat-submitting'); pd.body.classList.remove('kw-open'); }
    // 제출 시작(엔터·전송버튼·추천칩 클릭) 즉시 인사말/키워드 숨김 → 처리 중 잔상 제거
    // kw-open을 같이 지우는 이유: body.chat-submitting(숨김)과 body.kw-open(노출) 규칙은
    // CSS 명시도가 완전히 같아서(둘 다 class 1개 + [data-testid] + :has 안 attribute) 두 클래스가
    // 동시에 걸리면 스타일시트에 나중에 나오는 kw-open 쪽이 이겨 칩이 안 사라졌다(실측 확인된 버그).
    // 입력창 포커스 → 타이핑 → 엔터로 제출하는 게 일반적인 흐름이라 kw-open이 이미 켜진 채로
    // 제출되는 경우가 사실상 항상이었음. 제출 시점엔 kw-open을 무조건 꺼서 이 경합을 없앤다.
    function markSubmitting(){ pd.body.classList.add('chat-submitting'); pd.body.classList.remove('kw-open'); }
    var ta=pd.querySelector('[data-testid="stChatInput"] textarea');
    if(ta && !ta.dataset.subbound){
      ta.dataset.subbound='1';
      ta.addEventListener('keydown', function(e){ if(e.key==='Enter' && !e.shiftKey) markSubmitting(); });
    }
    var sb=pd.querySelector('[data-testid="stChatInputSubmitButton"]');
    if(sb && !sb.dataset.subbound){ sb.dataset.subbound='1'; sb.addEventListener('click', markSubmitting); }
    Array.prototype.forEach.call(pd.querySelectorAll('[class*="st-key-sugg_"] button:not([data-subbound])'),
      function(b){ b.setAttribute('data-subbound','1'); b.addEventListener('click', markSubmitting); });
    localizeCalendar(pd);
    Array.prototype.forEach.call(
      pd.querySelectorAll('.action-btn:not([data-ev])'),
      function(btn){
        btn.setAttribute('data-ev','1');
        btn.addEventListener('click',function(){
          var self=this;
          var title=self.getAttribute('title')||'';
          function flash(){
            self.style.color='#0B8457';
            self.style.borderColor='#0B8457';
            self.style.background='#E3F6EC';
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
          }else if(title==='다시 시도'||title==='좋아요'||title==='싫어요'){
            var idx=self.getAttribute('data-idx');
            var prefix=title==='좋아요'?'px_like_':title==='싫어요'?'px_dislike_':'px_retry_';
            var rb=pd.querySelector('.st-key-'+prefix+idx+' [data-testid="stButton"] button');
            if(rb){rb.click();flash();}
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

# ── 모델 결정 (선택 UI는 표시하지 않음) ─────────────────────────────
model = st.session_state.selected_model
_models = llm.models_for(provider)
if model not in _models:
    model = _models[0]
    st.session_state.selected_model = model

# ── 사용자 입력 처리 ─────────────────────────────────────────────────
# _chat_input 은 위(대화 렌더링 앞)에서 이미 생성했다. 여기서는 재사용만 한다.
# accept_file=True라 chat_input은 문자열이 아니라 ChatInputValue(text, files)를 반환한다.
_retry_prompt = st.session_state.pop("_retry_prompt", None)
_chip_prompt = st.session_state.pop("_chip_prompt", None)
prompt = (_chat_input.text if _chat_input else None) or _retry_prompt or _chip_prompt
uploaded_files = _chat_input.files if _chat_input else []

if prompt:
    st.session_state.pop("_show_txn_link", None)  # 새 메시지 입력 시 이체내역 링크 정리
    if _chat_input or _chip_prompt:
        # _retry_prompt는 conv["messages"]에 이미 있는 메시지를 재사용하는 것뿐이라 여기서
        # 또 추가하면 중복된다 — _chat_input(직접 입력)과 _chip_prompt(추천 칩)만 새로 추가.
        conv["messages"].append({"role": "user", "content": prompt})

    # ── 은행업무 에이전트 경로 (로그인 + OpenAI) ──────────────────────
    if agent_enabled:
        if not _retry_prompt:
            with st.chat_message("user"):
                st.markdown(prompt)
        with st.chat_message("assistant"):
            _thinking_ph = st.empty()
            _thinking_ph.markdown(_thinking_indicator_html("요청을 처리하고 있어요…"), unsafe_allow_html=True)
            try:
                result = agent.run_agent(
                    conv["messages"], openai_key=openai_key, model=model,
                    token=auth_token, system_prompt=system_prompt.strip() or None,
                )
            except Exception as e:
                result = {"kind": "message", "text": f"처리 중 오류가 발생했습니다: {e}"}
            _thinking_ph.empty()
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
        _thinking_ph = st.empty()
        _thinking_ph.markdown(_thinking_indicator_html(), unsafe_allow_html=True)
        try:
            _stream = llm.stream_chat(
                provider=provider,
                api_key=api_key,
                model=model,
                messages=llm_messages,
                temperature=temperature,
                system_prompt=system_prompt.strip() or None,
                attachments=attachments,
            )
            # 첫 토큰이 도착하는 순간(=응답 대기가 끝나는 순간)까지만 커스텀 아이콘을 보여준다.
            # next()로 첫 청크를 직접 당겨온 뒤, 그 청크를 다시 앞에 붙여 write_stream에 넘긴다
            # (제너레이터는 한 번 소비하면 되돌릴 수 없어 청크를 잃지 않으려면 이렇게 이어붙여야 함).
            _first_chunk = next(_stream)
            _thinking_ph.empty()

            def _resume_stream():
                yield _first_chunk
                yield from _stream

            response = st.write_stream(_resume_stream())
        except StopIteration:
            _thinking_ph.empty()
            response = ""
        except Exception as e:
            _thinking_ph.empty()
            response = None
            st.error(f"응답 생성 중 오류가 발생했습니다:\n{e}")

    if response:
        conv["messages"].append({"role": "assistant", "content": response})
        storage.save_conversation(conv)
        if cache_enabled and openai_key:
            cache.store(prompt, response, openai_key)
        st.rerun()
