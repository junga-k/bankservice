# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 실행 환경

Python 3.12 전용. 시스템 기본 `python3`는 3.8이라 사용하지 않는다. 명령은 항상 `.venv/bin/*`로 실행한다.

```bash
/usr/local/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # 키 입력
```

## 이 저장소는 하나가 아니라 여러 프로세스로 구성된다

한 앱이 아니라 **협력하는 여러 서비스**다. 기능을 온전히 테스트하려면 대부분 여러 개를 동시에 띄워야 한다.

| 프로세스 | 포트 | 실행 | 역할 |
|---|---|---|---|
| FastAPI 백엔드 | 8000 | `.venv/bin/uvicorn backend.app:app --port 8000` | 정적 사이트(`site/`) 서빙 + 은행 REST API. `docker http.server` 불필요 |
| Streamlit 챗봇 | 8501 | `.venv/bin/streamlit run app.py` | AI 챗봇 UI. 사이트에 iframe으로 임베드됨 |
| 이체 워커 | — | `.venv/bin/python transfer_consumer.py` | Kafka 소비 → 이체 실제 처리. **안 띄우면 이체가 `pending`에서 멈춘다** |
| Kafka | 9092 | `docker compose up -d` (docker-compose.yml, kafka만) | 이체 이벤트 큐 (`transfer-requests`) |
| Elasticsearch | 9200 | 별도 기동(compose에 없음) | RAG 색인 `rag_documents` |
| Redis | 6379 | 별도 기동 | 시맨틱 캐시 L1 |
| Phoenix | 6006 | `.venv/bin/phoenix serve` | OTEL 트레이스 수신(선택) |

모든 외부 서비스는 **없어도 앱이 죽지 않도록** try/except로 감싸져 있다(연결 안 되면 기능만 비활성/degrade).

### 초기 데이터 시드
`db.init_db()`가 서버 기동 시 스키마 생성 + 컬럼 마이그레이션(`ALTER TABLE ADD COLUMN` 멱등 루프)을 수행한다. 데모 데이터는 스크립트로 채운다:
```bash
.venv/bin/python seed_bank.py       # 사용자·계좌·거래·이체 (admin/admin1234, demo/demo1234)
.venv/bin/python seed_support.py    # 공지·FAQ·서식
.venv/bin/python seed_usage.py      # 이용 통계 이벤트
.venv/bin/python ingest_fss.py      # FSS 금융상품 → Elasticsearch 색인
```

## 테스트 / 배치

```bash
.venv/bin/pytest                              # 전체 (tests/)
.venv/bin/pytest tests/test_rag.py::test_x    # 단일 테스트
.venv/bin/python batch_test.py                # 1000문항 품질 배치(OpenAI, 20 동시)
.venv/bin/python build_stats.py               # 배치 결과 → site/data/stats.json (Backoffice 성능관리에서 조회)
```

## 아키텍처 큰 그림

### 두 계층으로 나뉜다
- **`backend/`** = FastAPI REST + 데이터/인프라 계층. `backend/app.py`가 엔드포인트, `db.py`(SQLite `bank.db`), `auth.py`(JWT), `kafka_io.py`, `health.py`, `infra_metrics.py`. 여기서 `rag.py`/`fss_fetcher.py`도 호출한다.
- **루트 `*.py`** = Streamlit 챗봇 + LLM/RAG/캐시/검색 모듈. `app.py`가 UI, 나머지는 단일 책임 모듈.

`app.py`는 `llm.stream_chat()`, `agent.run_agent()`, `rag.*`, `cache.*`, `search.*`, `storage.*`, `tracing.init_tracing()`만 호출한다. 제공자·저장·검색 세부는 각 모듈 내부에만 둔다.

### `llm.py` — LLM 추상화
- `PROVIDERS` 딕셔너리가 제공자·모델·secrets 키를 정의. 공개 API는 `stream_chat(...)` 하나(제너레이터로 청크 yield).
- **현재 활성 제공자는 OpenAI뿐**(`DEFAULT_PROVIDER="OpenAI"`). `_stream_gemini()`는 남아있으나 `PROVIDERS`에서 빠진 **비활성/데드 코드**다.
- 새 제공자: `PROVIDERS`에 추가 → `_stream_<provider>()` 작성 → `stream_chat()` 분기 연결. `app.py` 수정 불필요.

### `agent.py` — 은행업무 AI 에이전트 (도구호출)
- 단일 오케스트레이터가 OpenAI function-calling으로 백엔드 REST API를 **도구**로 호출. 도구는 도메인별 그룹(`ACCOUNT_TOOLS`/`TRANSFER_TOOLS`/`PRODUCT_TOOLS`/`SUPPORT_TOOLS`)으로 정의 → 추후 다중에이전트 전환 대비.
- 도구는 `BACKEND_URL`(:8000)로 HTTP 호출하며, 인증 필요한 도구는 사용자 JWT를 Bearer로 전달.
- **`propose_transfer`는 이체를 실행하지 않는다.** 예금주·수수료만 확인한 '제안'을 반환하고, 실제 실행은 `app.py`의 확인 카드에서 사용자가 버튼을 눌러야 `execute_transfer`→`POST /api/transfer`가 호출된다.
- 상품 추천은 `search_products`가 `/api/products`(상품안내 페이지와 동일 소스)를 조회 → 반환 목록 안의 상품만 안내하도록 지시.

### 이체 흐름 (동기 API + 비동기 워커)
`POST /api/transfer` → `db.create_transfer`(status=`pending`) → Kafka 발행. **`transfer_consumer.py`** 가 소비해 `db.process_transfer`로 출금계좌 차감 + 거래내역(`transactions`) 기록 + 상태를 `completed`/`failed`로 갱신한다. 즉 **잔액·거래내역 반영은 워커가 해야 일어난다.**

### RAG / 캐시
- `rag.py`: Elasticsearch `rag_documents`에 BM25+kNN 하이브리드 검색. `search(query, openai_key)`가 참고 텍스트를 반환. 색인은 `ingest_fss.py`가 채움.
- `cache.py`: 2계층 시맨틱 캐시 — Redis(L1 정확일치) + ChromaDB(L2 유사도). Phoenix 스팬 속성 `cache.hit`로만 히트 여부 기록(집계 저장 없음).

### 프런트엔드 (`site/`)
- 정적 SPA(`index.html` + `js/main.js` + `css/style.css`). 섹션 토글 방식, 인증은 localStorage(JWT). Backoffice(관리자) 탭에 대시보드·이체모니터링·이용통계·성능관리 등.
- `bankBadge()`가 은행명 옆 배지 렌더 — `site/img/banks/<파일>` 로고가 있으면 로고, 없으면 색상 배지로 자동 대체.

### 사이트 ↔ 챗봇 로그인 연동
사이트는 로그인 JWT를 챗봇 iframe URL에 `?token=`으로 전달(`main.js` `chatSrc`/`syncChatAuth`). 챗봇(`app.py`)은 `_SITE_EMBEDDED`일 때 이 토큰을 **단일 기준**으로 매 실행 동기화한다(로그인=토큰/로그아웃=빈값). 그래서 에이전트가 로그인 사용자 자격으로 은행 API를 호출한다. `:8501` 직접 접속 시에만 사이드바 수동 로그인 노출.

## 설정 파일 이원화 (자주 헷갈리는 부분)

- **`.streamlit/secrets.toml`** — Streamlit 챗봇 + 루트 스크립트(`ingest_fss.py` 등)가 읽음. `GEMINI_API_KEY`/`OPENAI_API_KEY`.
- **`config.json`** — 백엔드/`config.py`가 읽음. `fss_api_key`, `openai_api_key`, `rag_top_k`, `es_host`, `redis_*`, 챗봇 기본 설정 등. Backoffice 성능관리에서 편집.
- ⚠️ **주의**: `ingest_fss.py`는 FSS 키를 `secrets.toml`(또는 `--fss-key`)에서 찾는다. 키가 `config.json`에만 있으면 `--fss-key "$(...config...)"`로 넘기거나 secrets에 추가해야 한다.
- 둘 다 `.gitignore` 대상(`config.json`, `.streamlit/secrets.toml*`). 커밋 시 `git add -A` 대신 파일을 명시적으로 스테이징한다.

## 기타 설계 결정

- **Temperature preset**: 슬라이더 대신 "정확/균형/창의" selectbox(`llm.TEMP_OPTIONS`).
- **웹 검색 컨텍스트 분리**: 검색 결과는 LLM 메시지에만 주입, 대화 JSON(`conversations/<uuid>.json`)에는 원본 질문만 저장.
- **Phoenix 선택적**: 없어도 앱 동작. OpenAI는 `OpenAIInstrumentor` 자동계측, `rag`/`cache`는 수동 스팬. `batch_test.py`는 별도 프로젝트명으로 `phoenix.otel.register()` 직접 호출.
