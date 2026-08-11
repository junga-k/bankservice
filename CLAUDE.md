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

## 코드 수정 시 반드시 지킬 원칙 (반복 사고 방지)

2026-08-10 세션에서 "요청하지 않은 부분이 자꾸 바뀌거나 오류가 반복된다"는 지적을 받고 정리한 원칙.
그 세션에서 실제로 있었던 4가지 패턴(부수 효과로 버튼 텍스트 줄바꿈 버그 발생, 검증 없이 "완료"라고
보고해 여러 차례 왕복, 요청 범위를 벗어난 동시 수정, 코드 직접 호출로 검증한 유일한 사례만 한 번에
해결됨)에 근거해 앞으로 지킬 것:

- **범위 최소화**: 요청받은 것만 고친다. 고치다가 "이것도 같이 손보면 좋겠다"가 보여도 조용히 같이
  고치지 말고 먼저 물어본다(예외: 요청받은 버그의 직접 원인인 경우만).
- **공용 컨테이너에 구조적 CSS 금지**: `stColumn`/`stVerticalBlock`/`stHorizontalBlock`처럼 앱 전체에서
  재사용되는 Streamlit 컨테이너에 `width`/`flex`/`display` 같은 레이아웃 구조 속성을 직접 걸지 않는다.
  `margin`/`gap`처럼 다른 곳으로 새는 영향이 적은 속성을 우선 쓰고, 꼭 구조 속성이 필요하면
  `:has(.st-key-<특정위젯키>)`로 최대한 좁게 스코프한다.
- **"완료" 전에 실제로 검증**: AI은행원 iframe은 브라우저 자동화 클릭/타이핑이 안 먹히는 환경 제약이
  있다. 화면 스크린샷에만 의존하지 말고 **가능하면 코드를 직접 호출해서 재현·검증**한다(예:
  `agent.run_agent()`를 스크립트로 직접 부르기, `curl`로 API 직접 때리기, DB 직접 조회). 이 방식을 쓴
  사례가 화면 스크린샷 왕복보다 훨씬 빠르고 확실하게 원인을 잡았다.
- **검증 못 했으면 "확인했다"고 말하지 않는다**: 라이브 검증이 불가능하면 "이 부분은 검증 못 했다"고
  명시하고 사용자에게 직접 확인을 부탁한다 — 확실치 않은데 "완료됐다"고 보고하는 게 반복 왕복의
  가장 큰 원인이었다.

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
- site에는 `--font-display: "Cafe24 Ssurround"`(로고 워드마크 전용, 2026-08-12부로 Black Han Sans에서 교체 — "매치뱅크" 문구에만 쓰여서 실제 4글자만 남긴 서브셋을 `site/fonts/`에 자체 호스팅)와 `--text-xs`(12px)~`--text-3xl`(40px) 스케일이 정의돼 있음. 챗봇(`app.py`)에는 이 스케일이 없고 값을 그때그때 하드코딩(예: 칩 13px, 유의사항 11.5px) — 화면을 늘릴수록 이 격차가 문제될 수 있음.

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


## 신규 작업 규칙

새로운 컴포넌트나 화면을 만들 때는 반드시 `docs/tokens.md`에 정의된 값만 참조해서 만든다. 토큰에 없는 임의의 색상/간격/radius 값을 새로 만들어내지 않는다. 필요한 값이 토큰에 없다면, 임의로 만들지 말고 먼저 사용자에게 물어본다.

## Figma에서 detach된 인스턴스 — 마스터 컴포넌트 수정 시 반드시 확인

Figma 플러그인 API가 일부 컴포넌트 인스턴스 자식의 geometry(위치/크기)를 오버라이드하지 못하게 막는 경우가 있어(`resize()`가 에러 없이 조용히 무시되거나 `x` 등에 "This property cannot be overridden in an instance" 에러), 그럴 때 `detachInstance()`로 일반 프레임으로 바꿔서 우회해왔다. **detach된 노드는 그 순간부터 마스터 컴포넌트와 연결이 완전히 끊긴다** — 나중에 `Card`, `Ranked Bar` 등 마스터의 색상/스타일/구조를 바꿔도 이 노드들엔 자동 반영이 안 되니, 마스터를 고칠 때마다 아래 목록을 확인해서 수동으로 동기화할 것. 전체 목록·경위는 `session-log.md`의 "detachInstance() 사용 이력" 섹션 참고.

## 작업 기록 / 할 일은 다른 파일에

- **세션별 상세 작업 로그**(과거 조사·시행착오·스크린샷 비교 등)는 `session-log.md`.
- **아직 처리 안 된 작업 체크리스트**는 `backlog.md` — 새 세션은 여기부터 확인.
- Figma 디자인시스템 작업은 자동 메모리(`figma-design-system-progress.md`)에도 다음 세션용 요약이 있으니 참고.
