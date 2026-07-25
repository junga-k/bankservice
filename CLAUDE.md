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

## 화면 리디자인 작업 시 항상 참고할 외부 레퍼런스

화면을 새로 만들거나 리디자인할 때는 디자인 스킬(`ui-ux-pro-max`/`frontend-design` 등) 호출과 별개로 아래 사이트를 실제로 브라우저로 열어 참고한다. 사용자가 매번 링크를 다시 알려주지 않아도 되도록 여기 남겨둠.

- **uibowl.io**, **mobbin.com** — 화면 디자인 자체(레이아웃·구성·비주얼)의 레퍼런스. 좌측 사이드바 카테고리 체크박스(uibowl)나 상단 카테고리/Screens 필터(mobbin)로 관련 화면을 찾는다. 업종이 매치뱅크(금융)와 달라도 스타일 방향이 맞으면 참고 대상 — 금융권으로 한정하지 않는다. mobbin 무료 티어는 최신 3~4개만 열람 가능(나머지는 페이월)하니 막히면 다른 앱으로 전환.
- **uidesign.tips/ui-tips** — 개별 UI/UX 원칙·주의사항 모음(터치 타깃 크기, 위험한 액션 구분, selected state 표시, 카드 클릭 가능성 표시, 폼 라벨 등). 화면을 "보기 좋게"뿐 아니라 "사용성 좋게" 만들 때 실제로 적용할 체크리스트로 쓴다 — `get_page_text`로 전체 텍스트를 한 번에 읽는 게 스크린샷보다 효율적. 실제로 이 목록의 "Validate Deletion" 원칙에서 마이페이지 즐겨찾기 삭제에 확인창이 빠져 있던 버그를 발견한 적 있음(2026-07-25).
- 위 세 사이트에서 얻은 통찰은 매치뱅크 기존 디자인 시스템(그린 브랜드 톤, 카드 rest→hover 포뮬러, 8px 스페이싱, 알약형 대신 8px radius 등 — 아래 표 참고)과 상충하지 않는 선에서만 반영한다. 레퍼런스의 스타일을 그대로 이식하지 않고, "이 프로젝트라면 어떻게 적용할지"로 번역해서 쓸 것.

## 리디자인 디자인 시스템 (현재 기준값)

리디자인 작업(2026-07-20~)에서 실제로 정착된 컬러·타이포·사이즈 값. 새 화면을 리디자인할 때는 아래 값을 그대로 재사용하고, 새 값이 필요하면 이 표를 먼저 갱신한다.

**컬러 (site/css/style.css `:root`, `app.py` `:root`에 이름 동일하게 중복 선언)**
- Primary: `--blue: #0FA968` (매치뱅크 로고 그린), hover/강조 `--blue-dark: #0B8457`
- Primary soft: `--blue-soft: #E3F6EC`(연한 배경), `--blue-line: #A8E0C4`(연한 테두리/보더)
- 중립: `--bg-soft: #F8FAFD`, `--border: #DDE3EA`, `--text: #3C4043`, `--text-sub: #5F6368`
- semantic(상태): `--success`(=blue) / `--warning: #B45309` / `--error: #DC2626` / `--info: #2563EB` — 각각 `-soft` 배경 버전 존재
- ⚠️ 알려진 불일치: AI은행원 추천 칩(`app.py`)의 테두리색은 `#E3E6EA`로 하드코딩돼 있어 `--border`(`#DDE3EA`)와 미묘하게 다름 — 다음에 손댈 때 변수로 통일할 것.

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
- 전체 화면 CTA 버튼 크기 조정은 "다른 화면 리디자인을 다 끝낸 뒤 재검토"하기로 보류 중.

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

**남은 작업 (TODO)**
- CTA 버튼 전체 크기 재검토 — `#home`/`#products`/`#account`/`#auth`까지 끝났으니 재검토 시점 후보
