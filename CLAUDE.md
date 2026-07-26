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

Kafka·Elasticsearch·Phoenix 3개를 매번 따로 띄우는 대신 `./start_infra.sh`로 한 번에 기동할 수 있다(이미 떠 있는 서비스는 건드리지 않음). Docker Desktop이 "일시정지(paused)" 상태면 `docker desktop start`로는 안 풀리고 `docker desktop restart`를 써야 하는데, 이 스크립트가 그 판단까지 자동으로 해준다.

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
- **`backend/`** = FastAPI REST + 데이터/인프라 계층. `backend/app.py`가 엔드포인트, `db.py`(SQLite `bank.db`), `auth.py`(JWT), `kafka_io.py`, `infra_metrics.py`. 여기서 `rag.py`/`fss_fetcher.py`도 호출한다.
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

- **Temperature preset**: `llm.TEMP_OPTIONS`에 "정확/균형/창의" 3단계가 정의돼 있지만, 관리자가 고를 수 있는 UI는 2026-07-26부로 없앴다 — 금융 서비스 특성상 답변은 항상 `llm.DEFAULT_STYLE`("정확", temperature 0.2)로 고정. `TEMP_OPTIONS` 자체는 남겨둠(정책이 바뀌면 되돌리기 쉽도록). 참고로 `app.py`(Streamlit) 쪽엔애초 최종 사용자가 스타일을 고르는 위젯이 없었다 — 이 설정은 Backoffice 관리자 화면(`#bo-cc-*`)에서만 존재했다.
- **웹 검색 컨텍스트 분리**: 검색 결과는 LLM 메시지에만 주입, 대화 JSON(`conversations/<uuid>.json`)에는 원본 질문만 저장.
- **Phoenix 선택적**: 없어도 앱 동작. OpenAI는 `OpenAIInstrumentor` 자동계측, `rag`/`cache`는 수동 스팬. `batch_test.py`는 별도 프로젝트명으로 `phoenix.otel.register()` 직접 호출.

## 화면 리디자인 작업 시 항상 참고할 외부 레퍼런스

화면을 새로 만들거나 리디자인할 때는 디자인 스킬(`ui-ux-pro-max`/`frontend-design` 등) 호출과 별개로 아래 사이트를 실제로 브라우저로 열어 참고한다. 사용자가 매번 링크를 다시 알려주지 않아도 되도록 여기 남겨둠.

- **uibowl.io**, **mobbin.com** — 화면 디자인 자체(레이아웃·구성·비주얼)의 레퍼런스. 좌측 사이드바 카테고리 체크박스(uibowl)나 상단 카테고리/Screens 필터(mobbin)로 관련 화면을 찾는다. 업종이 매치뱅크(금융)와 달라도 스타일 방향이 맞으면 참고 대상 — 금융권으로 한정하지 않는다. mobbin 무료 티어는 최신 3~4개만 열람 가능(나머지는 페이월)하니 막히면 다른 앱으로 전환.
- **uidesign.tips/ui-tips** — 개별 UI/UX 원칙·주의사항 모음(터치 타깃 크기, 위험한 액션 구분, selected state 표시, 카드 클릭 가능성 표시, 폼 라벨 등). 화면을 "보기 좋게"뿐 아니라 "사용성 좋게" 만들 때 실제로 적용할 체크리스트로 쓴다 — `get_page_text`로 전체 텍스트를 한 번에 읽는 게 스크린샷보다 효율적. 실제로 이 목록의 "Validate Deletion" 원칙에서 마이페이지 즐겨찾기 삭제에 확인창이 빠져 있던 버그를 발견한 적 있음(2026-07-25).
- **uxpirates.xyz(UX 해적단)** — Toss·Naver Pay·Coupang·Airbnb·Duolingo 등 실제 프로덕트 스크린샷을 심리학적 UX 원칙(손실 회피/사회적 증거/프레이밍 효과/자이가르닉 효과/가변적 보상 등)별로 태깅해 모은 한국어 Good/Bad UX 사례 모음(2026-07-26 추가). 특히 **Toss·Naver Pay는 매치뱅크와 동일한 국내 핀테크 도메인**이라 업종이 다른 uibowl/mobbin보다 더 직접적으로 참고 가능 — 원칙 이름(예: "손실 회피")으로 필터링해 해당 원칙을 실제로 어떻게 화면에 구현했는지 스크린샷으로 확인하는 용도.
- 위 네 사이트에서 얻은 통찰은 매치뱅크 기존 디자인 시스템(그린 브랜드 톤, 카드 rest→hover 포뮬러, 8px 스페이싱, 알약형 대신 8px radius 등 — 아래 표 참고)과 상충하지 않는 선에서만 반영한다. 레퍼런스의 스타일을 그대로 이식하지 않고, "이 프로젝트라면 어떻게 적용할지"로 번역해서 쓸 것.

## 리디자인 디자인 시스템 (현재 기준값)

리디자인 작업(2026-07-20~)에서 실제로 정착된 컬러·타이포·사이즈 값. 새 화면을 리디자인할 때는 아래 값을 그대로 재사용하고, 새 값이 필요하면 이 표를 먼저 갱신한다.

**컬러 (site/css/style.css `:root`, `app.py` `:root`에 이름 동일하게 중복 선언)**
- Primary: `--blue: #0FA968` (매치뱅크 로고 그린), hover/강조 `--blue-dark: #0B8457`
- Primary soft: `--blue-soft: #E3F6EC`(연한 배경), `--blue-line: #A8E0C4`(연한 테두리/보더)
- 중립: `--bg-soft: #F8FAFD`, `--border: #DDE3EA`, `--text: #3C4043`, `--text-sub: #5F6368`
- semantic(상태): `--success`(=blue) / `--warning: #B45309` / `--error: #DC2626` / `--info: #2563EB` — 각각 `-soft` 배경 버전 존재

**타이포그래피**
- 본문 폰트: `IBM Plex Sans KR` (site·챗봇 동일, 챗봇은 Google Fonts `@import`로 로드)
- site에는 `--font-display: "Black Han Sans"`(강조용)와 `--text-xs`(12px)~`--text-3xl`(40px) 스케일이 정의돼 있음. 챗봇(`app.py`)에는 이 스케일이 없고 값을 그때그때 하드코딩(예: 칩 13px, 유의사항 11.5px) — 화면을 늘릴수록 이 격차가 문제될 수 있음.

**사이즈/모양 스케일 (site `:root`, "리디자인 Phase 0" 추가분 — 정의는 돼 있으나 아직 전면 적용은 안 된 상태)**
- spacing: 8px 기준 `--space-1`(4px)~`--space-9`(96px)
- radius: `--radius-sm`(8px) / 기존 `--radius`(14px, 카드 기본) / `--radius-lg`(20px) / `--radius-pill`(999px, 알약형)
- shadow: `--shadow-sm`(rgba(60,64,67,.06)) / `--shadow-md` / `--shadow-lg` / `--shadow-brand`(rgba(15,169,104,.28), 브랜드 그린 강조용)
- motion: `--ease: cubic-bezier(0.4,0,0.2,1)`, `--dur-fast: 150ms` / `--dur-base: 200ms` / `--dur-slow: 300ms`

**실제 컴포넌트에서 반복된 시각 패턴** (남은 화면 리디자인 시 그대로 확장 적용할 기준)
- **카드/버튼 rest→hover 공식**: rest = 흰 배경 + 연한 회색 테두리 + 아주 옅은 그림자(`--shadow-sm` 수준) → hover = 테두리가 브랜드 그린(`--blue-line`)으로 전환 + 그림자 확대(브랜드 그린 틴트) + 위로 2px 리프트(`translateY(-2px)`), `transition: 0.15s ease`. 백오피스 버튼, AI은행원 사이드바 버튼, 추천 칩에 공통 적용됨.
- **알약형(pill) 칩**: `border-radius: 18px`, `padding: 7px 14px`, `font-size: 13px`, `font-weight: 500`
- **사각 버튼/입력창**: `border-radius: 12px`
- **아이콘+텍스트 정렬**: 아이콘-텍스트 간격 `gap: 8px` 기준, 좌측 padding 14px

**Streamlit(`app.py`) 전용 CSS 작성 시 항상 주의할 점**
- Streamlit `key=`로 생성되는 `.st-key-<key>` 클래스는 버튼의 바로 위 부모가 아니라 그 바깥 `stElementContainer`에 붙는다. CSS 선택자에 직계 자식 결합자(`>`)를 쓰면 조용히 안 먹으니 항상 후손 결합자(space)를 쓸 것 — 이 세션에서만 3번 재발한 버그.
- Python/CSS를 고쳐도 이미 열린 세션에 반영이 안 될 때가 잦다. 브라우저 새로고침보다 `streamlit run` 프로세스 자체를 재시작하는 편이 확실하다.

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

### 2026-07-23

**기획서(`docs/기획서.md`) 작성 + Figma Slides 12장 미러링** — 대상: `docs/기획서.md`(신규), Figma Slides 파일
- 매치뱅크를 포트폴리오/회사 제출용으로 재정리하기 위해 리디자인(와이어프레임·디자인시스템)보다 기획서를 먼저 작성하기로 순서를 정함 — "왜 만들었는지"가 이후 모든 비주얼 결정의 근거가 되도록.
- 기획서는 표지 + 10섹션(프로젝트개요/문제정의/타겟/핵심가치/차별점/주요기능/IA/기술스택/성공지표/부록) 구성. 기술 용어(LangGraph, FastAPI, RAG 등)는 고유명사 그대로 쓰되, 그 의미는 PM·디자이너 등 비개발 직무도 이해할 수 있게 풀어씀.
- 타겟은 "포트폴리오를 보는 채용담당자"가 아니라 문제정의에서 바로 도출한 실제 제품 사용자(다중은행 상품비교가 번거로운 소비자 + AI Ops가 필요한 서비스 운영 담당자)로 재정의 — 문서 독자와 제품 타겟을 혼동하지 않도록 구분.
- 성공지표는 실사용자 지표(가입자수·만족도) 대신, 실제로 구축한 품질 검증 인프라(`batch_test.py` 1000문항 자동채점, Phoenix 트레이스 기반 캐시 히트율 실측, 프롬프트 A/B 테스트 구조)를 "어떻게 측정했는지" 방법론과 함께 서술하는 방식으로 정직하게 재정의.
- 기술스택 섹션은 "① 자체 구축 시스템"과 "② 외부 연동"(OpenAI AI 모델, 금융감독원 데이터)으로 구분 — FastAPI처럼 이름에 "API"가 들어간 프레임워크가 외부 데이터 소스와 헷갈리지 않도록 명확히 분리.
- 동일 내용을 Figma Slides 12장으로 미러링(포트폴리오 열람용, 리디자인이 아니라 기획서를 슬라이드화한 것). 매치뱅크 실제 브랜드 컬러(그린 `#0FA968`)·Noto Sans KR 적용, 배치마다 겹침/텍스트클리핑/영역이탈 자동검증 + 스크린샷 육안 확인.
- 다음 단계 순서 확정(별도 세션에서 진행 예정): 로컬에 설치된 claudekit 디자인 스킬(`ui-ux-pro-max`로 컬러·타이포 방향 결정 → `frontend-design`으로 `site/` 코드 직접 리디자인)로 실제 코드를 먼저 완성한 뒤, `figma-generate-library`/`figma-generate-design`으로 그 코드베이스에서 Figma 디자인시스템/화면을 역추출 — 기획서(md)→Figma Slides와 같은 "코드/문서가 원본, Figma는 그 결과물" 패턴.

### 2026-07-24

**백오피스 UI 일관성 정리** — 대상: `site/index.html`, `site/js/main.js`, `site/css/style.css`
- 공지사항/FAQ/서식관리/회원관리/금융상품관리의 검색창을 `.bo-search-inline` 공통 클래스로 통일 — 세로 길이 축소, 탭바와 같은 줄 오른쪽 정렬, 제목/등록일 메뉴와 10px 여백.
- 버튼 크기 전반 축소 + 오른쪽 정렬(`.bo-content .btn`, `.bo-btn-right`, `.bo-toolbar-right`, `.bo-row-actions`).
- 공지/FAQ/서식 목록에 "수정" 버튼 신규 추가(기존 "삭제"와 나란히) — `backend/db.py`에 `update_notice`/`update_faq`/`update_document`, `backend/app.py`에 `PUT /api/admin/notices/{id}` 등 3개 엔드포인트 신규.
- 대시보드 카드 정렬 버그(`align-items: start`로 패널 높이 안 맞던 문제), 도넛차트 좌측 쏠림(`justify-content: center` 누락) 수정.
- 이체모니터링/보안관리 테이블에 실제 데이터 기반 스파크라인 추가(`sparklineSvg`/`bucketByDay`, 장식용 가짜 데이터 아님) — 최근 이체 최대 300건을 집계해 일별 추세 표시.
- 체크박스가 거대한 정사각형으로 깨지던 버그(`.tf-field input` 일반 규칙이 checkbox까지 덮어씀) 수정.

**백엔드 헬스체크 40초+ 행(hang) 수정** — 대상: `backend/health.py`, `backend/infra_metrics.py`
- Kafka/Elasticsearch 클라이언트 라이브러리가 자체 `timeout` 설정을 무시하고 내부 재시도하는 문제 발견 → `ThreadPoolExecutor` + `future.result(timeout=N)`로 감싸 응답 없는 백그라운드 스레드를 포기(abandon)하는 방식으로 우회.

**AI은행원(챗봇) 사이드바/입력창 리디자인** — 대상: `app.py`
- "새 채팅"/"채팅검색" 버튼 스타일 통일(테두리 제거, 좌측정렬, 호버 시 배경색, 아이콘·텍스트 위치 픽셀 단위로 정렬).
- 사이드바 배경 제거(Streamlit 기본 테마 `secondaryBackgroundColor`가 강제 적용되던 것을 `!important`로 재정의).
- "AI은행원 유의사항" 폰트 크기 불일치 수정 — 컨테이너가 아니라 Streamlit이 `<li>`/`<ul>`에 직접 박아둔 16px를 명시적으로 덮어써야 했음.
- 입력창 클릭 시 뜨는 추천 키워드 칩을 2×3 그리드 → 가로 스크롤 한 줄(스크롤바 숨김)로 재구성, 흰 배경+연한 테두리+호버 시 그림자·리프트·테두리 그린 전환 스타일 적용.
- **반복 발견된 버그 패턴**: Streamlit `key=` 로 생성되는 `.st-key-*` 클래스는 버튼의 바로 위 부모가 아니라 그 바깥 `stElementContainer`에 붙는다 — CSS에서 직계 자식 결합자(`>`)를 쓰면 조용히 안 먹는다(이번 세션에서만 3번 재발: 새채팅 버튼, 채팅검색 입력창, 추천 칩). 항상 후손 결합자(space)를 써야 함.

**남은 리디자인 대상 화면 (TODO, 2026-07-25 시점 기준으로 아래 최신 항목 참고)** — 이 목록은 2026-07-25 세션에서 `#home`/`#products`/`#account`/`#auth`가 모두 진행되며 상당 부분 해소됨. 최신 상태는 아래 "### 2026-07-25" 항목 끝의 TODO 참고.

### 2026-07-25

**`#home` 히어로/레이아웃 리디자인** — 대상: `site/index.html`, `site/css/style.css`, `site/js/hero3d.js`(신규)
- 히어로에 신뢰 지표(16개 은행/5종 금융상품/금융감독원 데이터) 추가, 이모지 아이콘 전부 SVG로 교체, 쇼케이스 영역에 eyebrow 라벨 추가, 스크롤 리빌 애니메이션 적용.
- Three.js로 히어로 3D 장식 제작·반복(최종: "매치"를 상징하는 흐릿한 민트그린 구체 2개가 서로 붙었다 떨어졌다 하는 형태, 텍스트 뒤에 배치하고 `pointer-events:none`).
- 히어로 높이를 50vh로, CTA·퀵액션 카드 사이즈를 축소.
- ui-ux-pro-max/frontend-design/3d-web-experience 스킬 조사 결과를 반영해 방향 결정(민트그린 톤 유지, 과하지 않은 3D).

**`#products` 상품 리스트 리디자인** — 대상: `site/js/main.js`, `site/css/style.css`
- 상품 리스트 행 구조를 은행/카테고리(메타 행) + 상품명/금리(메인 행)로 재구성.
- TOP추천 배지를 연한 틴트 배경 → 브랜드 그린 그라디언트(진한 강조)로, 상품 리스트 행 자체는 박스/그림자 없는 구분선 기반 플랫 리스트로 변경(사용자가 두 옵션 모두 선택).
- **버그 수정**: TOP추천이 정렬/검색 상태에 따라 달라지는 `filtered[0]`을 기준으로 계산되고 있었음 — 카테고리 내 고정 최고금리 상품을 구하는 `getBestPick()` 헬퍼로 교체.

**`#account`(내 계좌) 리디자인** — 대상: `site/index.html`, `site/css/style.css`, `site/js/main.js`
- "계좌 등록" 버튼 추가(기존 마이페이지 계좌 탭으로 이동, `mypageGoTab` 재사용 — 새 로직 없음).
- 계좌 목록을 세로 스택 → 2열 카드 그리드로 변경, 카드에 큰 잔액 표시 + 거래내역/이체 퀵액션.
- 다른 화면과 컨테이너 폭을 맞추기 위해 1080px로 통일.

**`#auth`(로그인/회원가입/아이디찾기/비밀번호찾기) 전면 리디자인** — 대상: `site/index.html`, `site/css/style.css`, `site/js/main.js`, `backend/app.py`, `backend/db.py`
- 로고+브랜드명 표시, 레퍼런스 사이트를 참고해 로그인/회원가입 폼 재설계, 이메일 인증(인증번호 받기→입력→확인) 플로우를 실제 동작하듯 세심하게 구현(데모 OTP는 고정값이지만 화면에 노출하지 않음 — 프로덕션처럼 보이도록).
- **회원가입을 3단계 위저드로 분리**: 1단계 기본정보(아이디~이메일 인증), 2단계 계좌등록+이체비밀번호+약관동의, 3단계 완료 화면. 기존 이체 플로우의 `.transfer-steps`/`.tf-step` 패턴을 그대로 재사용.
- **비밀번호 찾기도 2단계 위저드로 분리**: 1단계 본인확인(아이디/이름/전화번호/이메일 인증), 2단계 새 비밀번호+새 비밀번호 확인(신규 필드). 프런트엔드에 비밀번호 길이(4자 이상)·일치 여부 사전 검증 추가 — 회원가입에서 겪은 것과 동일한 버그(프런트 검증 누락으로 백엔드에서만 걸러져 사용자가 원인을 못 보는 문제)가 재발하지 않도록 선제 조치.
- 아이디찾기는 전화번호 매칭 대신 이름+이메일 매칭으로 변경(`backend/db.py`에 `find_user_by_name_email()` 신규, `FindIdReq.phone` → `FindIdReq.email`).
- 비밀번호 관련 에러 메시지를 하단 상태문구가 아니라 각 비밀번호 입력 필드 바로 아래 작은 글씨(`.tf-hint`/`.tf-hint.err`)로 표시.
- AI은행원(`#chat`)에 로그인 게이트 추가 — 비로그인 시 안내만, 로그인 시 iframe(`#account` 게스트/인증 패턴 재사용).
- **치명적 버그 발견·수정**: `#auth { display:flex; ... }`가 바인딩 우선순위(specificity)에서 `.section{display:none}`을 이겨서, 로그인 화면이 활성 섹션과 무관하게 항상 렌더링되고 있었음(다른 섹션 아래/위에 겹쳐 보임). `#auth.active`로 스코프를 좁혀 수정 — 전 화면에서 재발 여부 확인 완료.
- 전체 화면 CTA 버튼 크기 조정은 "다른 화면 리디자인을 다 끝낸 뒤 재검토"하기로 보류했었으나, 소비자 화면·Backoffice 리디자인이 모두 끝난 뒤(2026-07-26) 재검토 여부를 물었을 때 사용자가 진행하지 않기로 결정 — 더 이상 TODO 아님.

**`#mypage`(마이페이지) 리디자인** — 대상: `site/index.html`, `site/css/style.css`, `site/js/main.js`
- 상단에 프로필 요약 카드 신규 추가(아바타 이니셜 + 이름 + `@아이디 · 가입일`), `loadMyProfile()`에서 `/api/me/profile`의 `created_at`을 함께 렌더링.
- 계좌 관리 카드의 은행명/계좌번호를 한 줄 텍스트에서 은행 로고-제목-부제 구조로 분리, 즐겨찾기·예약이체 항목도 이름/부가정보 계층 정리(예약이체는 예약·지연 상태 배지 추가).
- 빈 상태 문구를 폼 힌트용 `.tf-hint`(12px)에서 마이페이지 리스트 전용 `.mp-empty`(중앙 정렬·여백)로 분리 — 다른 화면(`#account` 예약이체 등)의 `.tf-hint` 재사용은 그대로 둠(동일 문구가 여러 곳에서 쓰여 첫 매치만 치환되지 않도록 스크립트로 스코프 확인 후 교체).
- 거래명세서 목록에서 선택되지 않은 행에 `opacity:0.55`를 줘 선택/미선택 상태를 시각적으로 구분(기존엔 hover 시에만 배경이 바뀌어 미선택 항목이 구분 안 됐음).
- **버그 수정**: 즐겨찾기 삭제(`mp-fav-del`)만 계좌 해지·예약이체 취소와 달리 `confirm()` 확인 절차가 빠져 있어 클릭 즉시 삭제되던 것을 발견, 동일하게 확인창 추가.
- ui-ux-pro-max/frontend-design 스킬 외에 사용자가 지정한 외부 레퍼런스 3곳(uibowl.io, mobbin.com, uidesign.tips)을 실제로 브라우저로 열어 참고 — 특히 uidesign.tips의 "Validate Deletion" 원칙에서 위 즐겨찾기 삭제 버그를 발견함. 이 워크플로우(스킬+레퍼런스 사이트 병행)는 이후 리디자인에도 계속 적용하기로 함.

**`#support`(고객센터) 본문 리디자인** — 대상: `site/index.html`, `site/css/style.css`, `site/js/main.js`
- 공지사항/FAQ 아코디언(`.faq-item`)을 항목별 박스+그림자 카드에서 구분선 기반 플랫 리스트로 변경 — 상품 리스트·마이페이지 즐겨찾기와 동일한 톤으로 통일. 열림 상태는 그림자 대신 텍스트 색상 변경 + 화살표 회전으로 구분.
- 공지/FAQ/서식 목록을 `.panel`로 감싸 마이페이지 즐겨찾기·예약이체와 같은 컨테이너 구조로 통일. 서식 카테고리 배지(`.doc-badge`)도 다른 화면의 알약형 배지 규격(`radius-pill`, 굵게)에 맞춤.
- 빈 상태 문구를 상품/AI은행원 안내용 범용 문구(`statEmpty()`)에서 맥락에 맞는 문구(`.support-empty`)로 교체 — 공지/FAQ/서식/문의내역 4곳.
- 레퍼런스: uibowl.io에서 금융권이 아닌 Whimsical(생산성 툴)의 FAQ 화면을 참고 — 업종이 달라도 이미 정착된 사이트 전체의 "박스 없는 플랫 리스트" 방향과 맞는 사례를 선택해 반영(사용자 요청: 업종 무관하게 스타일이 맞는 레퍼런스면 활용).

**폼 하단 저장/확인 버튼 오른쪽 정렬 통일** — 대상: `site/index.html`
- 이미 백오피스 5개 폼에만 쓰이고 있던 `.tf-form-footer` 유틸리티(`display:flex; justify-content:flex-end`)를 나머지 8개 폼에도 동일 적용 — 마이페이지 내정보/비밀번호/이체비밀번호/알림동의/계좌추가/즐겨찾기추가/회원탈퇴, 고객센터 문의등록.
- 히어로 CTA, 로그인/회원가입/아이디·비밀번호찾기 같은 CTA성 버튼, 이체 위저드의 다음/확인/이체실행 단계 버튼, 계좌번호 옆 인라인 "확인" 버튼, 모달 팝업의 전체폭 "확인" 버튼은 정렬 대상에서 제외(이미 각자의 컨벤션이 있음).

**버튼 radius 전체 통일 (마이페이지 기준)** — 대상: `site/css/style.css`
- 기본 `.btn`의 `border-radius`를 알약형(`--radius-pill`, 999px)에서 마이페이지가 쓰던 `--radius-sm`(8px)로 변경 — 탭/칩(`.cat-tab`, `.account-tab` 등)과 액션 버튼이 똑같이 알약형이라 구분이 안 된다는 피드백 반영. `#home .btn`만 예외로 알약형 유지(히어로 CTA).
- `#mypage .btn`에 중복돼 있던 `border-radius: 8px` 선언 제거(전역 기본값과 같아져 불필요), `#inquiry-form .btn`은 마이페이지와 동일한 컴팩트 사이즈로 축소.
- 헤더의 로그인/회원가입 링크(`.nav-auth`)는 애초에 `.btn` 클래스가 아니라 이번 변경과 무관함을 검토 중 확인.

**백오피스 스탯 카드: 스파크라인 → 미니 막대 차트 + 증감 배지** — 대상: `site/js/main.js`, `site/css/style.css`
- 이체모니터링(8칸)·보안 이벤트(4칸) 카드의 스파크라인이 축·값·툴팁 없이 색깔 있는 선만 보여줘 "관리자가 상황을 짐작만 해야 한다"는 피드백에서 시작. `dataviz` 스킬(스탯 타일 = value+delta+trend 계약)과 uibowl.io의 실제 대시보드 레퍼런스(축+툴팁+배지)를 참고해 방향을 잡고, Plan 모드에서 스파크라인/막대/미니멀 3안을 실제 사이트 톤 그대로 재현한 비교 목업(Artifact)을 만들어 사용자가 직접 고르게 함 — 사용자가 **미니 막대 차트**를 선택.
- `sparklineSvg`(선) → `sparkBars`(일별 미니 막대, 최신 막대만 accent색·나머지 de-emphasis)로 교체. 백오피스 대시보드 탭의 기존 "최근 14일 이용 추이" 막대차트와 같은 시각 문법이라 사이트 일관성 확보. 막대별 `title` 속성으로 날짜·값 네이티브 툴팁도 곁들임(보너스 채널, 필수 정보는 항상 보이는 캡션이 담당).
- `usageTrendBadge` → `trendBadge(current, previous, good)`로 일반화 — 기존엔 "증가=항상 좋음"으로 하드코딩돼 있어 실패/대기처럼 증가가 나쁜 지표엔 못 썼음. 화살표는 실제 증감 방향, 배지 색은 그 지표의 `good`(up/down/neutral) 기준으로 별도 결정(예: 실패 건수 증가는 ▲이지만 빨간 배지). 기존 대시보드 탭 호출부는 기본값으로 동작 100% 동일 유지(회귀 없음, 검증 완료).
- 12개 지표(이체 8 + 보안 4)를 `BO_TRANSFER_METRIC_META`/`BO_SECURITY_METRIC_META` 데이터 테이블 + 공유 `renderMetricGrid` 렌더러로 정리 — 기존엔 손으로 8/4개 분기를 치던 걸 대체. 이 과정에서 이체 쪽만 있던 `renderBoTransferSummary`의 이름 있는 함수 짝을 보안 쪽에도 `renderBoSecuritySummary`로 신설해 비대칭 해소.
- 각 카드에 "7/1 → 7/9 · 2건" 형식의 날짜범위+최근값 캡션 추가 — 축이 없는 대신 스케일과 최근 값을 항상 텍스트로 보여줌.

**로그인/회원가입 화면 스플릿 레이아웃 리디자인 + 버그 수정** — 대상: `site/index.html`, `site/css/style.css`, `site/js/main.js`
- "회원가입 폭이 좁아 답답해 보인다"는 피드백으로 시작. 480px 카드 하나가 넓은 데스크톱 화면 한복판에 덩그러니 떠 있던 게 원인 — `#auth`를 좌우 스플릿 레이아웃으로 재구성해 왼쪽에 브랜드 패널(그린 그라디언트 + 장식 블롭 + 홈 화면 신뢰 지표 재사용), 오른쪽에 기존 로그인/회원가입/아이디·비밀번호찾기 카드(480→520px로 소폭 확대) 배치. 960px 이하에서는 브랜드 패널을 숨기고 폼이 전체 폭을 차지하도록 반응형 처리.
- **브랜드명 폰트 삽질 기록**(다음에 비슷한 걸 또 겪지 않도록): 처음엔 브랜드명을 헤더와 맞춘다며 `--font-display`(Black Han Sans)에 `font-weight: 800`을 줬는데, 이 폰트는 Google Fonts에 굵기가 하나뿐이라 800을 줘도 브라우저가 억지로 두껍게 그려서(합성 볼드) 오히려 뭉개져 보였음. → 폰트를 IBM Plex Sans KR로 바꾸고 `font-weight: 600`으로 낮췄는데, 실제로 스크린샷으로 보니 여전히 얇아 보인다는 피드백 → `document.fonts`/`document.fonts.check()`로 확인한 결과 폰트 자체는 정상 로드됐지만, **IBM Plex Sans KR은 한글 글리프에서 400/500/600 굵기가 육안으로 거의 구분이 안 되고 700(Bold)부터 실제로 굵어지는** 폰트였음(0~800까지 실제 페이지에 나란히 렌더링해서 직접 비교 확인). → 결국 원래 원하던 방향(로고와 같은 폰트 유지)으로 되돌려 Black Han Sans + `letter-spacing: 2px`로 해결 — 두께가 아니라 자간이 좁아 글자가 뭉쳐 보이는 게 진짜 원인이었음. **교훈: 폰트 굵기를 조정할 땐 그 폰트가 실제로 그 굵기를 갖고 있는지(가변 폰트가 아니면 단일 굵기인 경우가 많음) 먼저 확인하고, 스크린샷만으로 판단하기 애매하면 여러 굵기를 실제로 나란히 렌더링해서 비교할 것.**
- **비밀번호 찾기 CTA 버그 수정**: STEP2("비밀번호 재설정") 버튼이 STEP1에서 이메일 인증만 완료하면 활성화돼버려서, STEP2 진입 직후 새 비밀번호를 하나도 안 쳤는데도 버튼이 활성 상태로 보이던 문제 발견. `updateResetPwButtonState()`에 새 비밀번호 두 칸이 4자 이상+일치해야 활성화되는 조건을 추가하고, STEP1 "다음" 버튼도 아이디찾기처럼 이메일 인증 전엔 비활성화되도록 `updateResetPwStep1ButtonState()` 신설.

**CTA 버튼 크기 재검토 + 계좌 추가 고스트 타일 + AI은행원 스피너/검색창 폭 미세조정** — 대상: `site/css/style.css`, `site/index.html`, `site/js/main.js`, `app.py`
- 전체 화면 재검토 결과 두 가지 실제 격차를 확정 반영: `.btn` 기본 padding을 `var(--space-4)`(16px)→`13px`로 낮춰 버튼 높이를 55px→49px로 축소(다른 화면 대비 CTA가 유독 커 보이던 문제), `#home .btn`은 기존 알약형 예외 유지.
- 계좌 목록 상단에 따로 떠 있던 "계좌 등록" 버튼(`.acct-header-row`)을 제거하고, 카드 그리드 안에 점선 테두리 고스트 타일(`.acct-card.ghost`)로 통합 — uibowl.io 지갑/계좌 대시보드 레퍼런스에서 "추가" 액션을 새로 만들어질 항목과 같은 자리에 두는 패턴을 확인하고 반영.
- AI은행원(Streamlit) 사이드바 "채팅검색" 입력창의 아이콘-텍스트 간격이 "새채팅" 버튼(8px)보다 넓어 보이던 버그 재수정 — 원인은 flex 부모의 `column-gap: 8px`는 이미 맞았는데 Streamlit `<input>` 자체의 기본 `padding-left: 12px`가 더해져 총 간격이 벌어진 것이었음. `padding-left: 0 !important` 한 줄로 해결(라이브 DOM에서 `getBoundingClientRect`/`getComputedStyle`로 정확한 원인 확인 후 수정 — 크로스오리진 iframe이라 부모 탭에선 안 보여서 `:8501` 직접 접속 탭에서 진단).
- AI 응답 대기 중 뜨는 Streamlit 기본 스피너(회색 링)를 브랜드 톤 알약형 배지(연한 그린 배경 + 은은한 펄스 애니메이션)로 재스킨 — `[data-testid="stSpinner"]` 기준으로만 스타일링해서 `st.spinner()` 호출부 어디서 어떤 문구를 띄우든 동일하게 적용됨. 스피너 링 자체(SVG 아님)는 재색상 시도 두 번(svg 셀렉터, svg * 셀렉터)이 모두 안 먹혀 원인을 못 찾았고, 효과 없는 추측성 CSS를 남기지 않기 위해 제거하고 회색 링은 그대로 둠(알려진 한계로 수용).

**스크롤 유도 힌트 (Prompt to Scroll)** — 대상: `site/index.html`, `site/css/style.css`, `site/js/main.js`
- uidesign.tips의 "Prompt User to Scroll" 팁(다음 섹션을 살짝 보여줘서 더 볼 내용이 있음을 알리는 것)을 참고해, 화면마다 콘텐츠를 직접 잘라 보여주는 대신 **콘텐츠 기반 범용 플로팅 힌트**로 구현 — 폼/표 위주의 유틸리티 화면(설정, 백오피스 탭 등)까지 억지로 "다음 섹션 미리보기"를 넣는 게 어색해서, 대신 현재 활성 섹션이 실제로 뷰포트를 넘칠 때만(`scrollHeight - innerHeight > 160`) 하단 중앙에 "스크롤하여 더 보기" 알약형 배지 + 통통 튀는 화살표를 노출하고, 스크롤을 시작하면 사라지는 방식.
- 기존 `navigate(name)` 함수 끝에 `updateScrollHint()` 훅을 추가해 SPA 섹션 전환마다 재평가 — 초기 로드(`navigate(location.hash...)`)도 같은 경로를 타므로 별도 처리 불필요. 우측 하단 `#scroll-top`(맨 위로) 버튼과 겹치지 않도록 하단 중앙에 배치.
- 브라우저에서 실측 검증: 긴 화면(`#home`, `#products`)에선 정상 노출·스크롤 시 정상 소멸·재진입 시 재노출 확인, 짧은 화면(백오피스 보안관리 탭, overflow 83px)에선 노출 안 됨 확인.

**Backoffice 9개 탭 성격별 분류 + 대시보드(Type A) 모니터링 우선 리디자인** — 대상: `site/index.html`, `site/css/style.css`, `site/js/main.js`
- 소비자 화면은 전부 리디자인을 마쳤지만 Backoffice 9개 탭은 "UI 정리" 수준에 머물러 있어 리디자인을 시작. "관리자 화면인만큼 모니터링·전체관리가 중요하다"는 사용자 지침에 따라 대시보드부터 "모니터링 우선 구조"로 재편하려 했으나, 이어진 사용자 피드백("프롬프트엔지니어링/상품관리/공지사항 같은 화면엔 대시보드가 안 맞는다")을 반영해 9개 탭을 **5개 성격 타입**으로 재분류: A(운영모니터링: 대시보드·이체모니터링), B(콘텐츠/CRUD 관리: 프롬프트엔지니어링·금융상품관리·FAQ·공지사항·회원관리), C(리포트: 이용통계), D(진단도구: 성능관리), E(읽기전용 정보: 시스템설정 — 이후 세션에서 D로 흡수·탭 자체 삭제됨, 아래 2026-07-26 항목 참고). 타입별로 다른 디자인 화법을 적용하기로 하고, A는 대시보드, B는 프롬프트 엔지니어링을 대표 구현으로 먼저 완료.
- **대시보드(Type A) 변경**: 시스템 상태 스트립(전체 헬스 배지 + Kafka/Elasticsearch/Phoenix/예약이체폴러 상태 칩 + "10초마다 자동 갱신" 캡션)을 최상단에 신설해 "지금 문제가 있는가"를 스크롤 없이 확인 가능하게 함. KPI 카드(총 이체 건수/완료금액/오늘 이용 이벤트)에 스파크라인+증감 배지 추가(이체모니터링 탭의 `renderMetricGrid`/`sparkBars` 재사용), 총 회원수/총 예치금은 시계열 API가 없어 트렌드 조작 없이 "누적" 라벨만 붙임(정직하게 처리). 은행별 이용 비중/이체 상태 분포 도넛 차트 2개를 막대 랭킹 리스트로 교체(`renderDonutChart` 삭제 → `renderRankedBars` 신규) — dataviz 스킬의 "donut은 deprioritized" 원칙과 uidesign.tips "Choose Chart Types Carefully" 팁이 독립적으로 같은 결론을 줌. 이체 상태 분포는 상태 토큰(`--blue-dark`/`--warning`/`--error`) 색상 사용. KPI 카드 일부를 클릭 가능하게 만들어 관련 탭(이체모니터링/회원관리)으로 바로 이동.
- 레퍼런스 조사 기록: uibowl.io엔 관리자 대시보드 패턴 사례가 없었음(정직하게 기록), mobbin.com에서 Quicken(재무관리 앱)의 "사이드바+카드그리드" 구조가 이미 쓰던 백오피스 골격과 같음을 확인, uidesign.tips에서 도넛→막대 근거 확보.

**프롬프트 엔지니어링(Type B 대표) 리디자인** — 대상: `site/index.html`, `site/css/style.css`, `site/js/main.js`
- **미저장 변경사항 배지 + 글자수 카운터**: 저장 폼의 어떤 필드든 바뀌면 저장 버튼 옆에 배지가 뜨고(로드/저장 시점 스냅샷과 비교), 저장 성공 시 사라짐 — 탭을 벗어나면 조용히 유실되던 문제 해결. 시스템 프롬프트 textarea 상단에 실시간 글자수 표시.
- **A/B 테스트 영역 시각 분리**: 배경 톤(`var(--bg-soft)`) + "실험 · 저장되지 않음" eyebrow 라벨로 "저장되는 설정"과 "테스트 중인 것"을 구조적으로 구분(대시보드의 "누적" 라벨과 같은 화법).
- **버전 이력 + 되돌리기 + diff**(사용자가 "프롬프트 관리에 뭐가 중요하냐" 질문에 직접 요청): 신규 `chatbot_config_history` SQLite 테이블(기존 `notices`류 CRUD 패턴 그대로 — 이 프로젝트에 히스토리 테이블 전례가 없어 새로 만듦) — 저장할 때마다 새 행 기록, "버전 이력" 패널에서 과거 버전과 diff 보기(순수 JS LCS 라인 diff, 외부 라이브러리 없음) 및 되돌리기(기존 공유 모달 컴포넌트를 백오피스에서 처음 활용) 가능. 되돌리기도 그 자체로 새 히스토리 행을 남겨 "되돌리기를 취소"할 수 있게 함.
- **모델/제공자 표시 개선**: 제공자는 `llm.PROVIDERS`에 OpenAI 하나뿐이라(Gemini는 미등록 비활성 코드) 드롭다운 대신 고정 텍스트로 전환. 모델은 원본 ID(`gpt-4o-mini` 등) 대신 "GPT-4 mini · 빠름·저렴" 같은 친숙한 이름+성능 티어로 표시(원본 ID는 hover 툴팁에 유지) — "모델만 봐선 성능차이를 모르겠다"는 피드백 반영. 버전 이력의 "모델·스타일" 칸도 긴 원문 텍스트 대신 짧은 배지(모노스페이스 모델 배지 + 색상 스타일 배지)로 정리.

### 2026-07-26

**답변 스타일 선택 제거(항상 "정확" 고정) + A/B 테스트를 프롬프트 비교로 재설계** — 대상: `llm.py`, `backend/app.py`, `site/index.html`, `site/css/style.css`, `site/js/main.js`
- 사용자 지적: "금융이면 정확하고 신뢰도 높은 정보를 제공해야 하는데 답변 스타일을 고를 필요는 없을 것 같다. A/B 테스트도 스타일이 아니라 새 프롬프트를 작성해서 질문을 입력하면 그에 따른 답변을 비교하는 게 더 필요한 기능이지 않을까?" — 실제로 조사해보니 답변 스타일 선택 UI는 이 백오피스 폼이 유일했고(`app.py`엔 최종 사용자용 스타일 위젯이 애초에 없었음 — CLAUDE.md의 기존 서술이 부정확했던 부분), 로그인 후 실제 은행업무 에이전트 경로(`agent.py`)는 이미 `temperature=0`으로 하드코딩돼 있어 이 설정과 무관했다 — 즉 스타일 선택 UI를 없애도 실질 영향은 작고 안전했다.
- `llm.py`에 `DEFAULT_STYLE` 상수 추가(`DEFAULT_PROVIDER`와 같은 패턴) — "정확" 모드로 고정. `TEMP_OPTIONS` 자체(균형/창의 포함)는 남겨둠(정책이 바뀌면 되돌리기 쉽도록), 관리자가 고를 수만 없게 함. 저장(`PUT /api/admin/chatbot-config`)과 되돌리기(`POST .../restore`) 양쪽 다 서버가 `default_style`을 항상 `llm.DEFAULT_STYLE`로 덮어써서 — 과거 버전이 실제로 "균형"이었어도 되돌리면 스타일만은 항상 정확 모드로 고정되는 불변식을 서버 쪽에서 보장. 버전 이력의 스타일 배지는 그대로 유지 — 과거(정책 변경 전) 기록은 정직하게 다른 값을 보여주고, 앞으로의 기록은 전부 "정확"으로 찍히는 게 정책 준수의 증거가 됨.
- A/B 테스트를 "스타일 A vs 스타일 B" → **"현재 저장된 프롬프트(A) vs 지금 편집 중인 프롬프트(B)"** 비교로 재설계 — 실제 프롬프트 엔지니어링 워크플로우(수정 → 저장 전 검증 → 저장)에 훨씬 맞음. "이 스타일 적용" 버튼 개념 자체가 사라짐 — B가 낫다고 판단되면 그냥 폼의 "저장"을 누르면 됨(이미 있던 미저장 변경사항 배지가 이 흐름을 자연스럽게 뒷받침). `boCcSavedPrompt`(마지막 로드/저장된 프롬프트) 변수를 새로 추적해 A값으로 재사용 — 신규 API 호출 없이 이미 있던 상태로 구현.
- 라이브 OpenAI 호출로 실제 A/B 비교가 서로 다른 응답·지연시간을 정확히 반환하는 것까지 실측 검증 완료.

**A/B 비교 결과 선택 버튼 + 버전 이력 스타일 컬럼 제거**
- A/B 프롬프트 비교 후 실제로 어느 쪽을 채택할지 고르는 버튼이 없다는 피드백 → A 컬럼엔 "이 대답으로 선택하기"(저장된 프롬프트로 되돌리기, `boCcSavedPrompt`를 textarea에 재적용), B 컬럼엔 동일 문구(제출 폼 `requestSubmit()` 재사용해 바로 저장) 버튼 추가. 처음엔 두 버튼이 서로 다른 스타일(ghost/primary)이었는데 "옆 버튼과 스타일을 맞춰달라"는 피드백으로 둘 다 ghost로 통일.
- 답변 스타일이 더 이상 선택 항목이 아니게 됐으니 버전 이력의 "모델·스타일" 컬럼에서 스타일 배지를 완전히 제거(헤더도 "모델"로 축소) — 죽은 코드(`boStyleShortLabel`, `.style-badge`, `BO_FIXED_STYLE`)도 함께 정리.

**외부 레퍼런스 재검토(uibowl.io/mobbin.com/Pinterest) + CRUD 폼 2열 레이아웃**
- "백오피스 화면도 레퍼런스 3곳 참고해서 만든 거냐"는 질문에 정직하게 답함 — 대시보드는 실제로 참고했지만 프롬프트관리/금융상품관리/FAQ 작업은 코드베이스 조사·사용자 지침·기존에 확보한 원칙 재적용으로 진행했고 레퍼런스 사이트를 다시 열어보진 않았음. 사용자 요청으로 재검토: uibowl.io는 "관리자"/"콘텐츠 관리"/"테이블" 검색 모두 무관한 결과(전과 동일한 결론), mobbin.com은 무료 티어 제한으로 확인 불가. 사용자가 직접 준 Pinterest 링크(`admin page design` 검색)에서 "Admin Dashboard: Edit Product" 핀 발견 — 짧은 필드(제목/구분)를 가로로, 긴 필드(설명)만 전체 폭으로 배치하는 2열 폼 패턴 확인.
- 금융상품관리의 서식 등록/수정 폼(제목+구분+설명)에 적용: 제목·구분을 기존 `.tf-row`(2열 그리드, 이미 프롬프트관리 폼에서 쓰던 클래스 재사용)로 묶고 설명만 아래 전체 폭. FAQ/공지사항 폼은 필드가 2개뿐이라(가로로 짝지을 상대가 없음) 대상에서 제외.

**금융상품관리·FAQ·공지사항 관리: 수정 중인 행 하이라이트 + 칩으로 목록 분리**
- Type B(목록+인라인폼) CRUD 화면의 핵심 갭이었던 "폼과 목록 사이 시각적 연결 없음"을 금융상품관리(서식관리)에 먼저 구현: 테이블 행에 `data-id` 추가 후 `boDocStartEdit`/`boDocCancelEdit`에서 `.editing` 클래스 토글(배경 틴트 + 왼쪽 강조선, `uidesign.tips` "Distinguish Selected Items" 재적용) — 이 프로젝트에 "선택된 행" 하이라이트 패턴이 전무했어서 새로 만듦. 같은 패턴을 FAQ·공지사항 관리(`boNoticeStartEdit`/`boFaqStartEdit`)에도 동일 적용.
- "FAQ와 공지사항이 한 화면에 나열돼 있다"는 피드백으로 `.bo-faq-tabs`/`.bo-faq-tab` 알약형 칩을 신설해 목록을 전환식으로 분리 — 기존 고객센터(`#support`)의 `.support-tab`과 같은 화법이지만, `.support-tab`은 전역 클릭 위임이 걸려 있어 그대로 재사용하면 고객 화면 로직과 충돌하므로 클래스를 별도로 새로 만듦(이후 상품관리의 `.bo-cat-tab`도 같은 이유로 분리).

**시스템설정 "시스템 현황" 카드 정렬 수정**
- 6개 카드가 `auto-fit` 그리드에서 5+1로 갈라져 마지막 카드가 혼자 떠 있던 문제 → `#bo-sys-overview`를 3열×2행으로 고정(900px 이하는 2열로 반응형).

**상품관리: 실시간 상품/금리 미리보기 + FSS 상태 요약, FAQ·공지사항 관리에 문의내역 칩 추가**
- "상품관리에서 관리자가 확인해야 할 다른 정보가 없을까"라는 질문에 조사해 갭 3개 제시 → 사용자가 전부 진행 확정:
  1. **실시간 상품 미리보기**: 상품관리 탭에 고객 페이지와 동일한 `/api/products`를 재사용한 읽기 전용 테이블 신설. 고객 쪽 렌더 함수(`renderProductList` 등)는 DOM id가 하드코딩돼 있어 그대로 재사용 불가 — 대신 DOM에 안 묶인 순수 로직(`getProductRateRange`, `bankBadge`)만 재사용해 아코디언/검색/정렬 없는 가벼운 확인용 렌더러를 새로 작성. 카테고리 전환은 `.bo-cat-tab`(전역 `.cat-tab`과 충돌 방지용 신규 클래스).
  2. **FSS 연동 상태 요약**: 성능관리 탭에만 있던 `/api/admin/fss-status`를 상품관리 탭 상단에도 한 줄 배지로 요약 노출 + "성능관리에서 자세히 보기" 버튼으로 연결(신규 백엔드 없이 기존 엔드포인트 재사용).
  3. **문의내역**: 고객은 문의를 남기고 마이페이지에서 조회할 수 있는데 관리자가 전체를 조회하는 화면이 백오피스 어디에도 없었음(확인 완료) — 사용자 제안대로 별도 탭이 아니라 FAQ·공지사항 관리의 세 번째 칩으로 추가. `inquiries` 테이블에 답변/상태 컬럼이 없어(순수 단방향) 이번엔 읽기 전용 전체 조회만 구현(`list_all_inquiries`/`count_all_inquiries` 신규, `users` 테이블 조인해 작성자 표시) — 응답 기능은 스키마 변경이 필요해 범위 밖으로 명확히 구분.
- 대출은 "최저~최고%" 오름차순, 예적금은 "최고 N%" 내림차순으로 고객 페이지와 동일한 정렬 방향까지 실측 확인.
- 위 3개 패널(상품 미리보기/FSS 상품 조회 통계/서식관리)이 한 화면에 쌓여 있던 것도 곧이어 "금융상품관리도 메뉴칩으로 구분해달라"는 요청으로 `.bo-product-tabs`/`.bo-product-tab`(FSS 요약 스트립은 칩과 무관하게 항상 상단 고정) 신설해 분리 — `.bo-cat-tab`이 이미 이 탭 안(상품 미리보기의 카테고리 전환)에서 쓰이고 있어 또 별도 클래스로 분리.

**시스템설정(Type E) 탭 삭제 → 성능관리(Type D)로 흡수** — 대상: `backend/app.py`, `site/index.html`, `site/css/style.css`, `site/js/main.js`
- "시스템설정에 현황/인프라 읽기 말고 더 보여줄 정보 없냐"는 질문에서 출발해 실제 내용을 뜯어보니 두 패널의 성격이 달랐음: "시스템 현황"(6칸 롤업)은 대시보드 KPI와 같은 API를 다시 조회해 같은 숫자를 보여주는 **완전 중복**, "인프라 설정(읽기전용)"은 성능관리의 "인프라 연동 현황"(상태)과 주제만 겹치고 내용은 설정값 vs 상태로 **보완 관계**였음. 이 분석을 사용자가 확인하고 시스템설정 탭 자체를 삭제, 인프라 설정값 + 신규 API 키 상태/AI은행원 요약을 성능관리로 흡수하기로 확정(시스템 현황 롤업은 중복이라 이관 없이 그냥 삭제) — 사이드바가 9개 → 8개 탭으로 줄어듦.
- `GET /api/admin/infra-config`가 이미 로드하는 `config.load()` 결과에서 `openai_key_set`/`fss_key_set`(값이 아니라 존재 여부만 반환 — 보안 원칙 유지)/`chatbot_provider`/`chatbot_model`/`chatbot_web_search` 5개 필드를 추가로 뽑아 응답에 포함(신규 API 호출 없음). `loadBoInfraConfig()`의 `rows` 배열 맨 앞에 이 5줄을 추가하고, API 키는 상태 배지(`status-badge ok/down`, 대시보드 시스템 상태 카드와 동일 톤)로, 모델은 기존 `boModelDisplay()`(GPT-4o→"GPT-4" 같은 친숙한 표기, 프롬프트관리에서 이미 구현된 것 재사용)로 렌더 — 값/배지 두 형태를 함께 처리하는 `boInfraRowValue()` 헬퍼 신설.
- `loadBoSystemOverview()`(대시보드와 100% 중복 조회)는 함수 자체를 완전히 삭제. `BO_TABS`에서 `"settings"` 제거, `ensureBoTabLoaded`의 `"settings"` 분기 삭제하고 `loadBoInfraConfig()` 호출을 `"perf"` 분기로 이동. `#bo-panel-settings` HTML 블록 전체 삭제, "인프라 설정" 패널만 `#bo-panel-perf`의 "시스템 상태" 바로 아래로 재배치(안내문 그대로 재사용). CSS는 `#bo-sys-overview` 전용 그리드 규칙(3열×2행 고정 — 바로 이전 세션에서 정렬 맞추려 만든 규칙) 삭제, 공유 폰트크기 셀렉터에서도 `#bo-sys-overview` 제거(`#bo-dash-summary`는 유지).
- 브라우저 실측 검증: 사이드바 8개 탭 확인, 성능관리 탭에 API 키 상태 배지·AI은행원 제공자/모델/웹검색 사용여부·기존 캐시/RAG/Redis/ES 6줄이 전부 정상 표시, 콘솔 에러 없음, `boGoTab("settings")`처럼 사라진 탭명을 호출해도 기존 안전장치(`if (!BO_TABS.includes(name)) name = "dashboard"`)가 그대로 작동해 대시보드로 안전하게 폴백하는 것까지 확인.

**회원관리(Type B) 리디자인 — 상태 배지 + 권한 필터 + 회원 상세(보유 계좌) 모달** — 대상: `backend/db.py`, `backend/app.py`, `site/index.html`, `site/css/style.css`, `site/js/main.js`
- 다른 Type B 화면(프롬프트관리/금융상품관리/FAQ·공지사항 관리)은 전부 "목록+인라인폼(CRUD)" 화법이었지만, 회원관리는 생성/수정/삭제가 없는 **순수 조회 전용 목록**이라 같은 화법(수정 중인 행 하이라이트 등)이 그대로 안 맞음 — CRUD 패턴을 억지로 이식하는 대신 이 화면에 실제로 부족한 정보가 뭔지부터 확인. 갭 3개 발견: (1) `users.is_active` 컬럼은 이미 있는데 목록 쿼리가 안 뽑아서 회원이 활성인지 탈퇴했는지 알 수 없었음, (2) 검색만 있고 관리자 계정만 걸러보는 필터가 없었음, (3) 총 계좌수/총 예치금은 전체 합계일 뿐이라 특정 회원의 보유 계좌·잔액을 볼 방법이 전혀 없었음.
- 이 프로젝트가 "회원 목록(password_hash 제외 — 개인정보/보안)" 주석까지 붙여가며 관리자 화면에서도 PII(이메일·전화번호) 노출을 의도적으로 피해온 것을 확인하고(문의내역 목록도 이메일 없이 이름만 노출), 이번 확장도 **PII는 그대로 제외**하고 DB에 이미 있는 비PII 정보(활성 상태, 계좌·잔액)만 추가하는 것으로 스코프를 한정.
- `db.list_users`/`count_users`에 `role` 파라미터 추가(검색 조건과 AND로 결합) + SELECT에 `is_active` 추가. 신규 `db.get_user_by_id()`(`get_user_by_username()`과 동일 패턴, password_hash/email/phone 제외 원칙 유지)와 신규 `GET /api/admin/users/{id}`(관리자 전용, 없으면 404) — 계좌 목록은 새 코드 없이 기존 `db.list_accounts(user_id)` 그대로 재사용.
- 프런트: 검색창 옆에 권한 필터 `<select>`(전체/관리자/일반회원) 추가, 목록에 `상태`(활성/탈퇴, `status-badge` — 성능관리 API 키 상태와 동일 톤) · `관리`(보기 버튼) 컬럼 추가. "보기" 클릭 시 FAQ·공지사항 관리의 문의내역 "보기"(`data-inquiry-view` → `showModal`) 패턴을 그대로 재사용해 회원 상세 모달을 띄우고, 그 안에 `bankBadge()`/`won()` 등 기존 헬퍼로 보유 계좌 테이블(은행/계좌번호/잔액/주계좌 배지)을 렌더.
- 레퍼런스 재확인: uibowl.io에서 "회원관리" 패턴 카테고리를 검색했으나 매칭되는 카테고리가 없었음(이전 세션의 "관리자 대시보드 패턴 없음" 결과와 동일하게 정직히 기록) — 대신 이 프로젝트에 이미 정착된 컴포넌트(상태 배지, 문의내역 상세 모달 패턴)를 그대로 재사용.
- 브라우저 실측 검증: 권한 필터를 "관리자"로 바꾸면 admin 계정 1건만 남는 것, "보기" 클릭 시 demo 계정의 계좌 3개(신한/국민/카카오뱅크)와 잔액이 API 응답과 정확히 일치해 렌더되는 것, 콘솔 에러 없음까지 확인.

**이체모니터링(Type A) — 이체 내역 계좌번호 검색 추가** — 대상: `backend/db.py`, `backend/app.py`, `site/index.html`, `site/js/main.js`
- 이 탭은 이전 세션들에서 이미 상당히 다듬어져 있었음(KPI 8+4칸 스파크라인+증감 배지, 보안 이벤트 좌측 상태 컬러바, 예약/지연 취소 버튼, 상태 필터 알약형 칩) — 그래서 시각적으로 새로 손대기보다 실제로 부족한 기능이 뭔지부터 확인. 다른 관리 목록 화면(회원관리·상품관리·FAQ·공지사항·문의내역)은 전부 검색창이 있는데 **이체 내역만 상태 필터뿐, 계좌번호로 찾는 방법이 없었음** — 이 한 가지 갭만 보완.
- `db.list_transfers`/`count_transfers`에 `q` 파라미터 추가(`from_account LIKE ? OR to_account LIKE ?`를 `status` 조건과 AND로 결합 — 회원관리에서 만든 다중 조건 조합 패턴 재사용), `admin_transfers()`에 `q` 스루. 프런트는 상태 필터 칩 위에 회원관리와 동일한 `.bo-search-inline` 검색 툴바 추가.
- 브라우저 실측 검증: `1002-333` 검색 시 해당 문자열이 출금 또는 입금 계좌번호에 포함된 행만 남는 것, 상태 필터와 동시 적용 시 AND로 좁혀지는 것(31건 → 13건) 확인. 콘솔 에러 없음.

**이용통계(Type C) — 조회/검색 비중 + 카테고리별 관심도 랭킹 추가** — 대상: `backend/db.py`, `backend/app.py`, `site/index.html`, `site/js/main.js`
- `usage_events`에 `bank`/`product`/`category` 컬럼이 모두 있는데, 대시보드가 이미 `bank` 기준 랭킹을, 이 탭이 `product` 기준 TOP5를 보여주면서도 **`category`(예금/적금/대출 3종) 단위 집계는 Backoffice 어디에도 없었음** — 이 갭을 신규 `db.stats_usage_by_category()`(`stats_banks()`와 동일한 GROUP BY 패턴)로 채움. `admin_usage_stats()` 응답에 `categories` 필드로 얹어서 새 엔드포인트 없이 확장.
- 조회(view)/검색(search) 건수도 요약 카드에 숫자로만 있고 상대적 비중이 안 보였음 — 대시보드가 이체 상태(완료/대기/실패)를 메트릭 카드 **+ 랭킹 막대** 두 방식으로 함께 보여주는 것과 동일한 화법을 적용, `renderRankedBars`(대시보드 은행 비중/이체 상태 분포에서 이미 쓰던 함수) 그대로 재사용해 신규 CSS 없이 두 패널(조회 vs 검색 비중/카테고리별 관심도) 추가.
- "최근 14일 이용 추이"(일자별 라벨+숫자 막대)는 이미 스파크라인보다 정보량이 많은 형태라 그대로 유지 — 다른 탭과 억지로 시각 통일하지 않음.
- 브라우저 실측 검증: 조회 79%(270건)/검색 21%(70건), 카테고리별 관심도 6개 항목(예금 38%~전세자금대출 7%)이 실데이터로 정확히 렌더되는 것, 합계 배지("총 340건"/"총 308건") 정상 표시, 콘솔 에러 없음 확인.

**성능관리(Type D) — 인프라 연동 현황 + LLM·RAG 성능 지표를 대시보드에서 이관** — 대상: `backend/health.py`(삭제), `backend/app.py`, `site/index.html`, `site/js/main.js`
- "성능관리에서 추가로 더 제공할 정보는 없어?" 질문에 다시 살펴보니, 대시보드 전용으로 만들어진 `/api/admin/infra-metrics`(`infra_metrics.py`)가 이미 계산해두고 있는데 정작 "성능관리" 탭엔 없는 정보가 있었음: (1) **예약이체 폴러 상태** — 성능관리의 "시스템 상태"는 `backend/health.py`의 단순 헬스체크(Phoenix/Kafka/ES 3개, ok/warn/down만)를 썼는데, 대시보드는 이미 `infra_metrics.py` 기반 4개(폴러 포함, 상세 수치 포함)를 씀. 대시보드 상단 배지도 여전히 `health.check_all()`(3개) 기준이라 바로 아래 칩 4개와 분모가 어긋나는 기존 버그까지 같이 발견(예: "0/3"인데 칩은 4개). (2) **LLM·RAG 실시간 성능 지표**(LLM 평균 응답 지연·요청 수·RAG 검색 횟수·캐시 히트율) — `infra_metrics.phoenix_metrics()`가 이미 계산하는데 "성능관리"라는 이름의 탭엔 전혀 없고 대시보드에만 있었음.
- 대시보드는 이미 최상단에 "지금 문제가 있는가" 요약 스트립(배지+칩)이 따로 있어서 그 아래 "인프라 연동 현황"(상태카드)·"LLM·RAG 사용 현황"(지표) 두 패널이 사실상 스트립과 내용이 겹침 — 시스템설정 탭 제거 때와 같은 논리(중복 삭제, 보완 정보는 목적에 맞는 탭으로 이관)로 이 두 패널을 대시보드에서 통째로 들어내 성능관리로 옮김. 대시보드엔 스트립(배지+칩)만 남음.
- `backend/health.py`는 이관 후 유일한 소비처(`admin_health()`)가 사라져 완전한 죽은 코드가 돼서 파일째 삭제(`app.py`의 `import health`, `GET /api/admin/health` 엔드포인트도 함께 제거) — `infra_metrics.py`가 상태(status/detail)까지 포함한 상위 호환 데이터라 남겨둘 이유가 없었음.
- `site/js/main.js`: 상태카드 4개 매핑 로직(`cards = [...]`)을 `buildInfraStatusCards()`/`renderInfraStatusCards()` 공용 헬퍼로 뽑아 대시보드 스트립(`loadBoDashboardInfra()`, 배지+칩만 남김)과 신규 `loadBoPerfInfra()`(성능관리의 "시스템 상태" 4카드 + "LLM·RAG 사용 현황" 지표, `#bo-health-cards`/`#bo-perf-llm-metrics`/`#bo-perf-llm-config`) 양쪽에서 재사용 — 로직 중복 없이 렌더 타깃만 다르게 호출.
- 브라우저 실측 검증: 대시보드 상단 배지가 "1/4"로 칩 개수와 일치하는 것, "인프라 연동 현황"/"LLM·RAG 사용 현황" 패널이 대시보드에서 사라진 것, 성능관리 "시스템 상태"가 예약이체폴러 포함 4카드로 뜨는 것, 새 "LLM·RAG 사용 현황" 패널이 실제 수치(현재 설정 요약 포함)로 채워지는 것, `/api/admin/health`가 404로 정리된 것, 콘솔 에러 없음까지 확인.

**스파크라인 KPI 카드: 항목명 위치 수정 + 전체 `.metric` 라벨 굵기 통일** — 대상: `site/css/style.css`, `site/js/main.js`
- Backoffice 전체 화면 브라우저 재검토 중 사용자가 스크린샷으로 지적: 스파크라인이 있는 카드(대시보드 5칸/이체모니터링 8칸/보안 이벤트 4칸)는 숫자 → 항목명 → 막대그래프 순서라 항목명이 숫자와 막대그래프 사이에 끼어 눈에 잘 안 띔.
- 새 CSS 모디파이어 `.metric--label-top`(`.metric`을 `flex-direction:column`으로 바꾸고 `.label`에 `order:-1`)을 신설해, DOM 순서(HTML 문자열)는 그대로 두고 시각적 순서만 항목명이 숫자 바로 아래·막대그래프 위로 오도록 재배치 — `renderBoDashboardSummary()`(대시보드 5칸)와 `renderMetricGrid()`(이체모니터링 8칸+보안 이벤트 4칸 공용 헬퍼)에 클래스만 추가.
- 처음엔 굵기(`font-weight:500`)도 `.metric--label-top .label`에만 스코프했는데, 사용자가 나머지 `.metric` 카드(회원관리·이용통계 요약, 성능관리의 LLM·RAG 지표/배치 테스트 성능/FSS 현황 등 — 스파크라인이 없어 위치는 원래도 문제없던 카드들)도 확인해달라고 해서 점검한 결과 그쪽 라벨은 여전히 400(regular)이라 같은 화면 안에서 굵기가 갈리는 걸 발견 — "다 500으로 통일해줘" 요청에 따라 굵기를 전역 `.metric .label` 규칙으로 올리고 모디파이어에서는 중복 선언 제거.
- 이어서 "다른 화면들도 폰트 굵기 일관성 확인해줘" 요청에 Backoffice 전체를 재점검(사용자가 범위를 "Backoffice 다른 화면들"로 한정) — `.metric` 외에도 같은 "값 옆 라벨" 역할인 `.rank-name`(대시보드 은행별 비중·이체상태분포, 이용통계 조회/검색 비중·카테고리별 관심도 랭킹 막대)과 `.topcat-list .p-name`(인프라 설정·FSS 카테고리·배치 테스트 카테고리·인기 상품 TOP5 리스트)가 굵기 지정이 없어 400인 것을 발견, 둘 다 500으로 통일. `.admin-table th`(이미 600)와 `.cf-row`의 `<b>` 값(굵은 태그)은 의도된 위계라 제외. `.topcat-list`는 상품안내(소비자 화면) 인기 상품 TOP5와 공유되는 컴포넌트라 그쪽에도 자연스럽게 반영됨(부작용이 아니라 같은 컴포넌트의 일관된 적용) — 브라우저로 확인.

**AI은행원 답변 평가(좋아요/싫어요) 실제 저장 + 프롬프트 관리 탭에 집계** — 대상: `app.py`, `backend/db.py`, `backend/app.py`, `site/index.html`, `site/js/main.js`
- "평가/다시/복사 버튼이 작동은 잘 되냐"는 질문에 `app.py`의 클릭 핸들러(`attach()`)를 뜯어보니, **복사**는 실제 클립보드 복사가 동작하고 **다시 시도**는 숨겨진 진짜 `st.button`을 프록시로 클릭해 재시도까지 실제로 되는데, **좋아요/싫어요는 `else{ flash(); }`로 그냥 2초간 초록색으로 반짝이기만 하고 아무 데이터도 저장되지 않는 순수 장식**이었음 — `backend/db.py`/`storage.py` 전체를 검색해도 feedback/평가 관련 테이블이 전혀 없어 확인 사살. 그래서 "백오피스에 집계해달라"는 요청도 애초에 저장되는 데이터가 없어 불가능한 상태였음.
- ChatGPT 등 리서치 결과를 참고해 두 가지 방향을 사용자와 확정: (1) 싫어요는 ChatGPT처럼 **비대칭 마찰**(좋아요는 원클릭, 싫어요는 이유 카테고리 선택까지) 적용, (2) 백오피스는 새 탭 대신 **프롬프트 관리 탭에 통합**(이미 AI 답변 품질을 다루는 곳이라 부정 피드백→시스템 프롬프트 수정→기존 A/B 비교 도구로 흐름이 자연스럽게 이어짐).
- 신규 `chat_feedback` 테이블(`bank.db`) + `POST /api/chat-feedback`(비로그인도 허용 — 챗봇 자체가 로그인 없이도 쓸 수 있어서)/`GET /api/admin/chat-feedback`(관리자 전용, 요약+이유별 집계+목록). `app.py`엔 숨겨진 진짜 버튼 `px_like_{i}`/`px_dislike_{i}`를 `px_retry_{i}`와 같은 패턴으로 추가하고, 싫어요는 바로 제출하지 않고 `st.form`으로 이유 체크박스+코멘트 입력을 펼친 뒤 제출 시 저장.
- **버그 하나 더 발견·수정**: 기존 "다시 시도" 프록시 클릭 로직(`msg.querySelector('[data-testid="stButton"] button')`, 숨겨진 버튼이 메시지당 1개일 때만 안전)을 그대로 뒀다면 좋아요/싫어요 버튼 추가로 숨겨진 버튼이 3개가 되면서 항상 첫 번째(엉뚱한) 버튼을 클릭하게 될 뻔했음 — `.action-btn`에 `data-idx`를 추가하고 `title`→`key` 접두사(`px_retry_`/`px_like_`/`px_dislike_`) 매핑으로 `.st-key-<prefix><idx>`를 정확히 타깃팅하도록 고쳐서 사전에 방지.
- CSS 숨김 규칙도 "메시지 안의 모든 버튼"에서 `[class*="st-key-px_"]`로 좁혀서, 싫어요 폼의 제출/취소 버튼(`px_`로 시작하지 않는 키)은 정상적으로 보이도록 분리. `.action-btn.active` 클래스로 평가 상태가 새로고침 후에도 계속 강조 표시되게 함(이전엔 2초 반짝임 후 사라져 뭘 눌렀는지 알 수 없었음).
- 브라우저 실측 검증(실제 대화로 진행): 좋아요 클릭 → 버튼이 초록색으로 영구 강조 + `bank.db`에 질문·답변 전문과 함께 저장 확인. 싫어요 클릭 → 이유 체크박스 폼이 펼쳐지고, 제출 시 좋아요 강조가 싫어요로 정확히 전환(마지막 평가만 활성 표시, 이력은 각각 새 행으로 남아 감사 추적 가능). "다시 시도"도 회귀 없이 정상 동작 확인. 백오피스 프롬프트 관리 탭에서 요약(총 평가/좋아요/싫어요/싫어요 비율)·이유 분포 랭킹·목록·상세 모달까지 실제 데이터로 확인, 콘솔 에러 없음.

**AI 피드백 관련 UI 마무리(정렬 + 폼 크기)**
- "관리" 컬럼 헤더(회원관리·AI 피드백 목록)가 왼쪽 정렬 텍스트인데 그 아래 "보기" 버튼은 `.bo-row-actions`(`justify-content:flex-end`)라 오른쪽에 붙어 헤더-버튼이 어긋나 보였음 — 다른 관리 화면(서식관리/FAQ/공지사항/문의내역)은 애초에 이 컬럼 헤더를 `<th></th>`(빈 칸)로 비워서 이 문제를 피해왔다는 걸 발견, 회원관리·AI 피드백 목록도 같은 방식으로 통일.
- `app.py`의 싫어요 이유 선택 폼(`st.form`)이 체크박스 몇 개뿐인데 채팅 컬럼 전체 폭(기본값, ~1000px+)으로 퍼져 보이던 것을 발견 — `st.form(key=...)`는 버튼/체크박스와 달리 컨테이너에 `st-key-*` 클래스가 안 붙는다는 걸 `:8501` 직접 접속해 실제 DOM으로 확인(`window.parent` 크로스오리진 우회 없이 바로 진단 가능 — 폼 자체는 iframe 안에서만 렌더되므로 애초에 부모 문서 접근이 필요 없었음), `[data-testid="stForm"]`을 직접 타깃팅해 `max-width:420px` + 내부 세로 간격(`stVerticalBlock` gap) 16px→8px로 축소.
- 이어서 "제출/취소 버튼 비율을 맞추고 오른쪽 여백을 줄여달라"는 요청으로 재조사 — `width`를 아무리 `!important`로 덮어써도 렌더링이 안 바뀌는 게 이상해서 `getBoundingClientRect()`로 실측 추적한 끝에 원인 확인: Streamlit이 콘텐츠 블록(`stVerticalBlock`)에 기본으로 `max-width: 82%`를 걸어두고 있었고(가독성을 위해 넓은 화면에서 텍스트 폭을 제한하는 Streamlit 자체 규칙), `width`를 얼마로 지정하든 CSS 스펙상 `max-width`가 항상 우선 적용되기 때문에 `width` 오버라이드는 애초에 무의미했음 — `max-width: 100% !important`로 그 규칙 자체를 무력화해야 했음. 이후 체크박스·입력창·제출/취소 버튼 줄이 전부 폼 오른쪽 끝까지 정확히 채워짐(두 버튼은 `st.columns(2)`라 애초부터 50/50 동일 비율이었고, 실제 문제는 버튼 자체가 아니라 그 버튼들을 담은 줄 전체가 폼 폭의 82%까지만 채워지던 것).
- 마지막으로 "질문/선택지 색상을 구분하고, 자유 텍스트란은 없애고, 기타 선택 시에만 입력창이 뜨게 해달라"는 요청 — `st.form`은 위젯 하나를 바꿔도 제출 전까진 스크립트가 재실행되지 않는 구조라 "기타 체크 시 즉시 입력창 노출" 같은 실시간 반응이 원천적으로 불가능했음. `st.form`을 걷어내고 `st.container(border=True, key=f"dislike_box_{i}")`로 교체(제출/취소도 `form_submit_button` 대신 평범한 `st.button`으로, 제출·취소 시 체크박스·기타입력 세션 상태까지 수동으로 초기화). 교체하면서 발견한 보너스: `st.container(key=...)`는 `st.form(key=...)`와 달리 컨테이너 자체에 `.st-key-<key>` 클래스가 정상적으로 붙어서, 앞서 `stForm`을 직접 타깃팅해야 했던 우회가 필요 없어지고 CSS가 더 간단해짐. 자유 텍스트(`추가로 남기고 싶은 말`) 삭제, "기타" 체크 시에만 `st.text_input("기타 사유를 입력해주세요")` 노출(폼이 아니므로 체크 즉시 재실행되어 바로 나타남). 캡션("어떤 점이 아쉬웠나요?")은 `--text-sub`(회색), 체크박스 선택지 텍스트는 `--text`+`font-weight:500`(진하게)로 색상 분리 — 기존엔 둘 다 동일한 `--text`라 구분이 안 됐음. 실제 대화에서 기타 체크→텍스트 입력→제출까지 전체 플로우 실측, `bank.db`에 `reasons:["기타"]`+입력한 코멘트가 정확히 저장되는 것 확인.
- "테두리가 텍스트·버튼에 너무 붙어 답답하다"는 마지막 피드백에 uidesign.tips(`get_page_text`로 전체 목록 확인, "Visually Separate Elements" — 여백이 구획을 나누고 인지 부담을 줄인다는 원칙)를 참고 — 실측해보니 `st.container(border=True)`의 기본 padding이 `0px 8px`(위아래 여백이 아예 0)였던 게 원인. `padding: 18px 20px !important`로 사방 여백을 주고 체크박스 사이 간격도 8px→10px로 살짝 넓혀 그룹 간 구분을 개선.
- 이유 문구 중 "말투가 별로"가 다소 직설적이어서 "말투/스타일이 아쉬움"으로 순화(`_DISLIKE_REASONS` 문자열만 변경, 저장 스키마 영향 없음).

### 2026-07-26 (계속) — 홈 배너/이벤트/특별상품 연동 완성 + 히어로 리디자인

**홈 배너 ↔ 공지사항/이벤트/특별상품 연동 완성 (신규 테이블 4개 + 이미지 업로드)** — 대상: `backend/db.py`, `backend/app.py`, `site/index.html`, `site/js/main.js`, `site/css/style.css`, `seed_support.py`, `requirements.txt`
- 위에 남아있던 마지막 TODO를 완성. `banners`/`events`/`event_entries`/`special_products` 4개 테이블 신설, 각각 `notices`류와 동일한 CRUD 패턴 + 공개/로그인/관리자 권한이 분리된 엔드포인트.
- **배너는 애초 계획했던 "테마 색상 select" 대신 실제 이미지 파일 업로드 방식으로 설계를 바꿈** — "실무에서는 포토샵/일러스트로 만든 배너 파일을 업로드하는 경우가 많다"는 피드백 반영. `UploadFile`+`Form` 기반 엔드포인트, `site/img/banners/`에 uuid 파일명으로 저장(원본 파일명을 경로에 그대로 안 써서 경로 조작 방지), 5MB/이미지 MIME 화이트리스트 검증. 배너 사이즈는 웹 검색 + 실제 네이버 타임보드 배너를 devtools로 직접 실측한 값을 참고해 최종 **1200×130px**로 확정, 스타일(radius 8px + 테두리 느낌 링 섀도우)도 네이버 실측값을 그대로 반영. Backoffice 폼의 파일 첨부 UI는 네이티브 `<input type=file>`이 브라우저마다 제각각이라 숨기고 커스텀 버튼으로 재구성, 노출순서도 자유입력 대신 1~5 select로 제한.
- 이벤트는 추첨 여부(`is_drawing`)를 고를 수 있고, "추첨 실행" 클릭 시 당첨자를 무작위 선정. **당첨자 발표는 원본 글에 끼워 넣지 않고 "[당첨자 발표] ..."라는 별도 게시글을 자동 생성**하는 방식으로 설계(실제 이벤트 운영 관행과 동일) — 사용자가 "이벤트 글과 당첨자 발표 글이 따로 있어야 한다"고 명시적으로 요청해 반영. 당첨자 이름은 전체 공개 게시물이라 서버에서 가운데를 마스킹(`관*자`)해서 내려줌.
- 특별상품(청년미래적금류)은 처음엔 "매치뱅크"라는 가상 은행 상품으로 시드했으나, 사용자 질문을 계기로 **청년미래적금 같은 정부정책 상품은 FSS가 아니라 서민금융진흥원이 관리해 FSS API 데이터에 아예 없다는 사실을 확인** — 실제 FSS 데이터 중 상품명에 "특판"이 들어간 진짜 상품(부산은행 "더(The) 특판 정기예금", 최고 3.55%)으로 교체해 데이터 정직성을 확보. 매치뱅크는 자체 은행이 아니라 FSS API 기반 비교 사이트라는 점을 재확인한 계기였음.
- Backoffice: 9번째 탭 "배너 관리" 신설, FAQ·공지사항 관리에 "이벤트" 칩(사용자 요청으로 목록 맨 끝 위치로 재배치), 금융상품관리에 "특별상품 관리" 칩 추가.
- **버그 수정**: 배너/이벤트 배지가 제목 옆이 아니라 멀리 떨어져 보이던 문제 — `.faq-q`류가 `justify-content:space-between`인 "본문+화살표" 2요소 레이아웃인데, 배지까지 더해 3요소를 직접 넣으면 각각 개별 flex item으로 흩어지는 게 원인이었음(배지+본문을 span 하나로 묶어서 해결) — 특별상품에서 먼저 겪고 이벤트에서 동일하게 재발.

**히어로 배너 리디자인: 진한 그린 배경 + 흰 구체 + 좌우 분할** — 대상: `site/css/style.css`, `site/js/hero3d.js`, `site/index.html`
- "임팩트 있게 만들 수 있을지" 질문을 계기로 기존 히어로(옅은 민트 그라디언트, 존재감 약함)를 진한 그린(`#0FA968→#0B8457`, 로고색을 그라디언트 시작점으로 사용)으로 전환, 텍스트·CTA 버튼 배색도 함께 반전(흰 배경+진한 초록 글씨)해 가독성 확보.
- 구체를 중앙 오버레이로 유지할지 좌우 분할로 바꿀지를 두고, 실제 사이트 톤 그대로 재현한 Artifact 비교 목업(A안/B안)을 만들어 사용자가 직접 고르게 함(백오피스 스탯카드 3안 비교 때와 동일한 워크플로우) — 사용자가 제공한 Pinterest 레퍼런스("Design That Lives With You" 그린 배경 좌우분할 카드)로 좌우분할이 실제로 흔히 쓰이는 검증된 패턴임을 확인한 뒤 B안(구체 왼쪽 전용 영역 / 텍스트 오른쪽) 선택.
- 구체 머티리얼을 `MeshStandardMaterial`(조명 반응 → 회녹색으로 탁하게 보임) → `MeshBasicMaterial`(조명 무관 → 순수 흰색)로 교체, 애니메이션 속도 2배, opacity 상향.
- **버그 수정**: 캔버스 블러가 `overflow:hidden` 경계에서 그대로 잘려 옅은 사각형 테두리처럼 보이던 문제 — `radial-gradient` 마스크로 캔버스 가장자리를 완전히 투명하게 처리해 해결.
- **버그 수정**: 헤더 내비게이션이 화면이 좁아지면 메뉴 글자가 단어 단위로 줄바꿈되며 2줄로 깨지던 문제 — `.nav a`에 `white-space:nowrap`이 없던 게 원인. `font-size`/`padding`을 `clamp()`로 화면 폭에 따라 유동적으로 줄어들게 해 줄바꿈 없이 항상 한 줄 유지(기존엔 680px 한 단계짜리 고정 breakpoint뿐이었음).

**사이트 footer 신설 + 홈 "기능 소개" 목업을 실제 화면 그대로 갱신** — 대상: `site/index.html`, `site/css/style.css`
- 사이트에 footer가 아예 없어서 신규 제작 — 브랜드/태그라인/"포트폴리오 목적 데모 프로젝트" 고지문, 바로가기·고객센터 링크(기존 `data-nav` 클릭 위임 그대로 재사용해 새 JS 불필요).
- 홈 화면의 "내 계좌"/"상품안내" 기능 소개 목업이 초기 버전 그대로(단순 리스트, 박스형 칩) 남아있던 것을 사용자가 지적 — 실제 `#account`(총자산 요약 + 2열 카드그리드 + 거래내역/이체 버튼)와 `#products`(TOP추천 그라디언트 카드 + 구분선 기반 플랫 리스트) 화면을 그대로 재현하도록 갱신. 이체 목업도 계좌번호를 나열하던 정적 폼 대신, 실제 이체 위저드의 완료 영수증 화면(체크마크+스텝 표시)으로 교체.

**"맨 위로 이동" 플로팅 버튼: 화살표 아이콘 교체 + 위치를 콘텐츠 컬럼 옆으로 이동** — 대상: `site/index.html`, `site/css/style.css`
- 기존 유니코드 텍스트 화살표(`↑`)를 굵기·길이를 독립적으로 조절할 수 있도록 stroke 기반 인라인 SVG(`stroke-width:2.8`, 짧은 세로선+화살촉)로 교체 — "화살표 굵기를 키우고 세로길이를 줄여달라"는 요청 반영.
- 버튼이 뷰포트 오른쪽 가장자리(`right:24px` 고정)에 붙어 있어 넓은 화면일수록 중앙 콘텐츠 컬럼(`--maxw` 1080px)과 멀리 떨어져 눈에 잘 안 띄던 문제 — `right: max(24px, calc((100vw - var(--maxw)) / 2 - 64px))`로 변경해 콘텐츠 컬럼 바로 바깥쪽 여백에 붙게 함(좁은 화면은 24px로 폴백).
- **버그 수정**: 처음 계산식에서 여백 오프셋을 더하는 방향(`+16px`)으로 넣어 오히려 버튼이 콘텐츠 컬럼 안쪽으로 들어가 카드 텍스트와 겹치는 문제 발생 — "겹치지 않게 오른쪽으로 옮겨달라"는 피드백으로 원인 파악, 부호를 반대로(`- 64px` = 버튼너비 46px+간격 18px) 고쳐 컬럼 가장자리 바깥쪽에 정확히 위치하도록 수정.

**전체 화면·메뉴 재검토 — 스크롤 유도 힌트 겹침 버그 수정 + AI은행원 칩 테두리색 통일** — 대상: `site/js/main.js`, `app.py`
- footer 추가 이후 디자인이 다 끝났는지 전체 화면(홈/헤더 반응형/로그인·회원가입/내 계좌/상품안내/고객센터/마이페이지/AI은행원/Backoffice 9개 탭)을 다시 훑어보며 점검.
- **버그 수정**: "스크롤하여 더 보기" 힌트(`updateScrollHint`)가 `document.documentElement.scrollHeight`(문서 전체 높이) 기준으로 노출 여부를 판단하고 있었는데, 사이트 전체 footer가 모든 화면 하단에 추가되면서 footer 높이만으로도 이 값이 항상 임계치(160px)를 넘어버려 Backoffice 대시보드처럼 원래 스크롤이 필요 없던 짧은 화면에서도 힌트가 항상 뜨고, 심지어 대시보드 카드·상품안내 패널 위에 겹쳐 보이는 회귀가 발생했음(원래 설계 의도는 "현재 화면 자체에 더 볼 콘텐츠가 있을 때만" 노출). `main > .section.active`의 `getBoundingClientRect().bottom`만 기준으로 삼도록 수정해 footer를 계산에서 제외 — Backoffice 대시보드·내 계좌는 힌트가 다시 안 뜨고, 상품안내처럼 실제로 섹션 자체에 더 볼 콘텐츠(특별상품 목록 등)가 있는 화면은 정상 노출되는 것을 확인.
- AI은행원 추천 칩의 테두리색이 `#E3E6EA`로 하드코딩돼 전역 `--border`(`#DDE3EA`)와 미묘하게 다르던 것(위 디자인 시스템 표에 "알려진 불일치"로 남아있던 항목)을 `var(--border)`로 교체해 통일 — Streamlit 프로세스 재시작 후 브라우저에서 확인.

**로고 마크 리디자인 — 둥근 사각형 배경 제거, 단일 원 배지 + 모노그램** — 대상: `site/img/logo-mark.svg`, `site/css/style.css`
- 기존 로고(`rx=9` 둥근 사각형 그린 그라디언트 배경 위에 흰 원 2개가 겹치는 모티프)가 "앱 내부 아이콘으로는 무난하지만 독립된 로고로는 임팩트가 부족하다"는 피드백 — 사각형을 없애고 원형만 남기되 브랜드 컬러를 쓰자는 요청에서 시작.
- 사용자가 제시한 안 외에 다른 방향도 함께 보고 고르고 싶어해, 4가지 후보(겹치는 두 원을 브랜드 그린 solid로/아웃라인 벤다이어그램/단일 원 배지+모노그램/미니멀 그라디언트 원)를 실제 헤더(흰 배경)·로그인 좌측 패널(그린 배경) 두 문맥과 16·32·64px 크기로 함께 렌더링한 Artifact 비교 목업을 먼저 제작 — 이 프로젝트의 기존 관례(백오피스 스탯카드 3안, 히어로 A/B 비교)를 그대로 따름. 사용자가 **단일 원 배지 + 모노그램**안을 선택.
- `logo-mark.svg`를 브랜드 그라디언트(`#0FA968→#0B8457`) 원 하나(`r=15`) 안에 축소된 흰색 렌즈 모노그램(겹치는 두 원 실루엣, opacity 0.9/0.55)을 담는 구조로 교체. 외곽에 아주 옅은 흰 stroke(`opacity 0.6`)를 둘러, 흰 배경(헤더·푸터·로그인 카드)에서는 티 안 나고 그린 배경(로그인 좌측 패널)에서는 은은한 경계를 만들어 두 문맥 모두에서 하나의 파일로 통용되게 함.
- **버그 방지**: 기존 `.auth-logo`/`.auth-aside-logo`에 걸려있던 `border-radius: var(--radius-sm)`(8px) + `box-shadow`는 예전의 정사각형에 가까운 배경(`rx=9`)을 전제로 넣은 스타일이었는데, 새 로고는 `r=15`짜리 원이 32×32 캔버스를 거의 꽉 채우고 있어 그대로 두면 8px 모서리 반경 클리핑이 원의 네 귀퉁이를 잘라내는 문제가 생길 뻔했음 — 두 규칙 모두 제거하고, 원형 알파 채널을 그대로 따라가는 `filter: drop-shadow(...)`로 교체해 사각 그림자가 원형 그래픽 밖으로 어색하게 남지 않도록 함.
- 브라우저로 헤더·푸터·로그인 카드·로그인 좌측 그린 패널 4곳 전부 확인 — 사각 배경 없이 원형 배지만 보이고, 그린 배경 위에서도 옅은 흰 테두리 덕분에 형태가 또렷이 구분되는 것을 확인.

**남은 작업 (TODO)**
- 현재 알려진 TODO 없음 — Backoffice 8개 탭 리디자인, 홈 배너/이벤트/특별상품 연동까지 전부 완료.
