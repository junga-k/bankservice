# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 실행 환경

Python 3.12 전용. 시스템 기본 `python3`는 3.8이라 사용하지 않는다.

```bash
# 최초 설치
/usr/local/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# API 키 설정
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# .streamlit/secrets.toml 에 GEMINI_API_KEY / OPENAI_API_KEY 입력

# 앱 실행
.venv/bin/streamlit run app.py

# Phoenix 추적 서버 (별도 터미널)
.venv/bin/phoenix serve

# 배치 테스트 (1000개 질문)
.venv/bin/python batch_test.py
```

## 아키텍처

### 모듈 역할 분리 원칙
`app.py`는 `llm.stream_chat()`, `storage.*`, `search.*`, `tracing.init_tracing()`만 호출한다. LLM 제공자 세부 구현, 저장 방식, 검색 엔진은 각 모듈 내부에만 존재한다.

### `llm.py` — LLM 추상화 (핵심)
- `PROVIDERS` 딕셔너리가 제공자 목록·모델·secrets 키를 정의한다.
- `stream_chat(provider, api_key, model, messages, ...)` 하나만 공개 API. 제너레이터로 텍스트 청크를 yield한다.
- **새 제공자 추가**: `PROVIDERS`에 항목 추가 → `_stream_<provider>()` 작성 → `stream_chat()` 분기 연결. `app.py` 수정 불필요.
- Gemini는 `google-genai` 신규 SDK(`from google import genai`) 사용. 구버전 `google-generativeai`와 다르다.
- Gemini는 OTEL 자동 계측 미지원이라 `_stream_gemini()` 내부에 수동 스팬을 직접 구현했다.
- `attachments` 형식: `{"type": "text"|"image", "name", "content"|"data", "mime_type"}`. 마지막 user 메시지에만 주입된다.

### `storage.py` — 대화 저장
- `conversations/<uuid>.json` 파일 하나당 대화 하나.
- 메시지가 없으면 저장하지 않는다(`save_conversation` 내 가드).
- 저장 경로는 `__file__` 기준 상대 경로라 실행 위치와 무관하게 동작한다.

### `tracing.py` — Phoenix 연결
- `@st.cache_resource`로 감싸져 앱 생애주기 동안 한 번만 실행된다.
- Phoenix 서버(`http://localhost:6006`)가 없어도 앱은 정상 동작하며, 사이드바에 안내 메시지만 표시된다.
- OpenAI는 `OpenAIInstrumentor`로 자동 계측, Gemini는 `llm.py` 내 수동 스팬으로 처리한다.
- `batch_test.py`는 Streamlit 없이 실행되므로 `tracing.py`를 import하지 않고 직접 `phoenix.otel.register()`를 호출한다.

### `search.py` — 웹 검색
- `ddgs` 패키지(DuckDuckGo) 사용. API 키 불필요.
- 검색 결과는 저장되지 않는다. LLM 전달용 메시지에만 주입되고 `conv["messages"]`(스토리지)에는 원본 질문만 저장된다.

### `app.py` — Streamlit UI 흐름
1. `tracing.init_tracing()` 호출 (캐시됨)
2. 사이드바: 제공자·모델·답변스타일(temperature preset)·시스템프롬프트·웹검색·파일첨부·대화목록
3. API 키 없으면 `st.stop()`으로 중단
4. 채팅 입력 시: 원본 메시지를 `conv["messages"]`에 추가 → 웹 검색 결과를 별도 `llm_messages`에만 주입 → 파일을 `attachments`로 변환 → `st.write_stream()` → 저장

## 주요 설계 결정

- **Temperature preset**: 슬라이더 대신 "정확(0.2) / 균형(0.7) / 창의(0.95)" selectbox. `_TEMP_OPTIONS` 딕셔너리가 `app.py` 사이드바 블록 안에 정의되어 있다.
- **웹 검색 컨텍스트 분리**: 검색 결과는 LLM에만 전달되고 대화 JSON에는 저장되지 않는다. 대화를 나중에 불러봐도 검색 결과 노이즈가 없다.
- **Gemini 쿼터**: 무료 등급은 일 1,500 RPD 제한. 배치 테스트 시 소진될 수 있다. OpenAI gpt-4o-mini는 배치에 적합(500 RPM).

## secrets.toml 구조

```toml
GEMINI_API_KEY = "..."   # aistudio.google.com
OPENAI_API_KEY = "sk-..." # platform.openai.com
```

두 키 모두 선택 사항. 없는 제공자를 선택하면 앱이 안내 메시지를 띄운다.
