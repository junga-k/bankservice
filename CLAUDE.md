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
- 상품 추천은 `search_products`가 `/api/products`(상품안내 페이지와 동일 소스, 예금/적금/주택담보대출/전세자금대출/신용대출 5종)를 조회 → 반환 목록 안의 상품만 안내하도록 지시.
- `tool_search_products`의 카테고리 자동판별: 명시값 → 질의어 키워드("전세"/"주택담보"/"신용대출"/"적금"/"예금"/"대출") → 기본 "금리비교"(예금+적금). 대출 카테고리는 `best_rate`가 최저금리 기준이라 예금/적금(최고금리 기준)과 의미가 반대임을 도구 응답 `note`에 명시해 LLM에게 알려준다.

### `fss_fetcher.py` — FSS 상품 데이터 파싱
- `PRODUCT_TYPES`에 예금/적금/주택담보대출/전세자금대출/신용대출 5개 카테고리가 모두 등록돼 있고, 전부 FSS Open API(`finlife.fss.or.kr`)에서 실시간 조회한다(더미 데이터 아님).
- **예금·적금과 대출 3종은 `optionList` 스키마가 완전히 다르다.** 예금/적금은 `save_trm`/`intr_rate`/`intr_rate2` 필드를 쓰지만, 대출은 `lend_rate_min`/`lend_rate_max`(주택담보대출·전세자금대출) 또는 `crdt_grad_1~13`+`crdt_grad_avg`(신용대출, 신용점수 구간별 금리)를 쓴다. 그래서 `fetch_category_structured()`는 `category in LOAN_CATEGORIES`면 별도 파싱 함수 `_fetch_loan_structured()`로 분기한다.
- 신용대출 `optionList`에는 `crdt_lend_rate_type_nm`이 "대출금리" 외에 "기준금리"/"가산금리"/"가감조정금리"(대출금리를 구성하는 요소)도 섞여 있다. 실제 적용금리만 쓰려면 `crdt_lend_rate_type_nm == "대출금리"`인 행만 필터링해야 한다(안 하면 가감조정금리 같은 작은 값이 최저금리로 잘못 집계됨).
- `_group_options()`는 옵션을 `(fin_co_no, fin_prdt_cd)` 복합키로 묶는다. `fin_prdt_cd`는 은행마다 독립적으로 매기는 코드라 다른 은행끼리 우연히 같은 코드를 쓰는 경우가 실제로 있어서(`fin_prdt_cd` 단독 매칭 시 서로 다른 은행의 금리 옵션이 섞임), 반드시 은행코드까지 포함해서 묶어야 한다.
- `backend/app.py`의 `/api/products`는 대출 카테고리면 `best_rate` 오름차순(최저금리 우선), 예금/적금은 내림차순(최고금리 우선)으로 정렬 — 대출은 금리가 낮을수록 유리해서 방향이 반대다.

### 이체 흐름 (동기 API + 비동기 워커)
`POST /api/transfer` → `db.create_transfer`(status=`pending`) → Kafka 발행. **`transfer_consumer.py`** 가 소비해 `db.process_transfer`로 출금계좌 차감 + 거래내역(`transactions`) 기록 + 상태를 `completed`/`failed`로 갱신한다. 즉 **잔액·거래내역 반영은 워커가 해야 일어난다.**

### RAG / 캐시
- `rag.py`: Elasticsearch `rag_documents`에 BM25+kNN 하이브리드 검색. `search(query, openai_key)`가 참고 텍스트를 반환. 색인은 `ingest_fss.py`가 채움.
- `cache.py`: 2계층 시맨틱 캐시 — Redis(L1 정확일치) + ChromaDB(L2 유사도). Phoenix 스팬 속성 `cache.hit`로만 히트 여부 기록(집계 저장 없음).

### 프런트엔드 (`site/`)
- 정적 SPA(`index.html` + `js/main.js` + `css/style.css`). 섹션 토글 방식, 인증은 localStorage(JWT). Backoffice(관리자) 탭에 대시보드·이체모니터링·이용통계·성능관리 등.
- `bankBadge()`가 은행명 옆 배지 렌더 — `site/img/banks/<파일>` 로고가 있으면 로고, 없으면 색상 배지로 자동 대체.
- 헤더 로고는 `site/img/logo-mark.svg`(아이콘만)/`site/img/logo.svg`(아이콘+워드마크) — 파비콘 등 다른 곳에서도 재사용 가능하도록 파일로 분리돼 있다(헤더 자체는 `logo-mark.svg`만 씀).
- `#products` 카테고리는 카드가 아니라 알약형 탭바(`.cat-tabs`/`.cat-tab`, 5개: 예금/적금/주택담보대출/전세자금대출/신용대출) — 설명 문구는 탭이 아니라 클릭 시 펼쳐지는 상품 목록 패널(`#product-list-desc`)에 표시된다. 목록이 길면 `renderProductList()`가 상위 `PRODUCT_LIST_PAGE_SIZE`(8)개만 보여주고 "더보기/접기" 토글로 나머지를 펼친다(`site/js/main.js`).

### 사이트 ↔ 챗봇 로그인 연동
사이트는 로그인 JWT를 챗봇 iframe URL에 `?token=`으로 전달(`main.js` `chatSrc`/`syncChatAuth`). 챗봇(`app.py`)은 `_SITE_EMBEDDED`일 때 이 토큰을 **단일 기준**으로 매 실행 동기화한다(로그인=토큰/로그아웃=빈값). 그래서 에이전트가 로그인 사용자 자격으로 은행 API를 호출한다. `:8501` 직접 접속 시에만 사이드바 수동 로그인 노출.

## 설정 파일 이원화 (자주 헷갈리는 부분)

- **`.streamlit/secrets.toml`** — Streamlit 챗봇 + 루트 스크립트(`ingest_fss.py` 등)가 읽음. `GEMINI_API_KEY`/`OPENAI_API_KEY`.
  - 키 발급: Gemini(무료 등급 있음) https://aistudio.google.com/app/apikey , OpenAI https://platform.openai.com/api-keys — 사용할 제공자 키만 있으면 됨(둘 다 필요 없음).
- **`config.json`** — 백엔드/`config.py`가 읽음. `fss_api_key`, `openai_api_key`, `rag_top_k`, `es_host`, `redis_*`, 챗봇 기본 설정 등. Backoffice 성능관리에서 편집.
- ⚠️ **주의**: `ingest_fss.py`는 FSS 키를 `secrets.toml`(또는 `--fss-key`)에서 찾는다. 키가 `config.json`에만 있으면 `--fss-key "$(...config...)"`로 넘기거나 secrets에 추가해야 한다.
- 둘 다 `.gitignore` 대상(`config.json`, `.streamlit/secrets.toml*`). 커밋 시 `git add -A` 대신 파일을 명시적으로 스테이징한다.

## 기타 설계 결정

- **Temperature preset**: 슬라이더 대신 "정확/균형/창의" selectbox(`llm.TEMP_OPTIONS`).
- **웹 검색 컨텍스트 분리**: 검색 결과는 LLM 메시지에만 주입, 대화 JSON(`conversations/<uuid>.json`)에는 원본 질문만 저장.
- **Phoenix 선택적**: 없어도 앱 동작. OpenAI는 `OpenAIInstrumentor` 자동계측, `rag`/`cache`는 수동 스팬. `batch_test.py`는 별도 프로젝트명으로 `phoenix.otel.register()` 직접 호출.

## 작업 기록

세션마다 진행한 변경을 날짜순으로 아래에 추가한다.

### 2026-07-20

**챗봇 사이드바 리디자인 (Gemini 스타일 flat list)** — 대상: `app.py`, `storage.py`
- "새 채팅"을 펜 아이콘 + 음영 있는 둥근 사각형 행으로 변경.
- "채팅 검색" 입력창 신규 추가 — 대화 제목뿐 아니라 메시지 본문까지 포함해 실시간 필터링(`storage.list_conversations(query=...)`).
- "이전 대화" 목록을 테두리 박스 없는 flat list로 정리, 삭제 아이콘은 행에 마우스를 올렸을 때만 표시.
- "AI은행원 유의사항" expander는 접고 펼치는 구조 유지, 정보 아이콘으로 통일하고 여백/폰트 축소.
- 버그 수정: 1차 구현에서 테두리가 제거되지 않던 문제 — Streamlit 프런트엔드 번들을 직접 분석해 원인 파악. `st-key-<key>` 클래스가 위젯이 아니라 바깥 `stElementContainer`에 붙는다는 점을 반영해 CSS 선택자를 후손 결합자로 교정하고, 존재하지 않는 testid(`stVerticalBlockBorderWrapper`)를 실제 testid(`stVerticalBlock`)로 교체.

**입력창 "+" 파일 업로드** — 대상: `app.py`
- Streamlit 네이티브 `st.chat_input(accept_file="multiple", file_type=[...])`로 구현(커스텀 팝업 대신 채택 — 위치/스타일을 Streamlit이 직접 보장해 신뢰성이 더 높음).
- 기존 지원 파일 형식(txt/md/py/js/ts/csv/json/html/css/pdf/png/jpg/jpeg/gif/webp)과 200MB 제한(서버 기본값) 그대로 유지.
- `chat_input`이 문자열 대신 `ChatInputValue(text, files)`를 반환하도록 API가 바뀌어 prompt/attachment 처리 로직도 함께 리팩터링.
- 사이드바의 중복되던 "파일 첨부" 섹션(`st.file_uploader`) 제거, 관련 CSS(`stFileUploaderDropzone`)도 정리.

**상단 메뉴 순서 변경** — 대상: `site/index.html`
- 홈 → 내 계좌 → 상품안내 → AI은행원 → 고객센터 순서를 홈 → AI은행원 → 내 계좌 → 상품안내 → 고객센터로 변경.
- 메뉴 밑줄 인디케이터는 클릭된 요소의 위치를 JS에서 동적으로 계산하는 방식이라 순서 변경과 무관하게 정상 동작(코드 변경 불필요).

**`#products` 인기 통계 랭킹 클릭 가능하게** — 대상: `site/js/main.js`, `site/css/style.css`
- 은행 순위 클릭 → 해당 은행 공식 사이트로 새 탭 이동. `site/data/banks.json`(은행명→URL) 매핑을 `banksData`로 캐시해 재사용, 매칭되는 은행만 `<a>`로 렌더링.
- 카테고리별 인기 상품 TOP5 클릭 → 상품안내 목록에서 해당 카테고리 탭을 자동 선택하고, 그 상품 행을 찾아 스크롤 + 상세 아코디언 펼침(`goToProductInList()`, 기존 `goto-account` 폴링 패턴 재사용). "더보기" 뒤에 숨어 있는 상품도 자동으로 펼쳐서 찾음.
- 두 기능 모두 백엔드/DB 변경 없이 프런트엔드만으로 구현(상품 랭킹 데이터엔 은행명이 없다는 것을 확인 후, 기존 두 개의 분리된 패널—은행 순위 / 카테고리별 인기 상품—구조에 맞춰 설계).
