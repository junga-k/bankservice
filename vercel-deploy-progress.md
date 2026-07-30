# Vercel 배포 진행 기록

## 현재 문제
SQLite가 Vercel 서버리스 읽기전용 파일시스템과 안 맞아서 배포(`bf6003a`)가 크래시남.
(`sqlite3.OperationalError: unable to open database file` — `backend/db.py:42`)

## 결정
Turso(libSQL)로 마이그레이션하기로 함.
1번 안(`/tmp`에 bank.db 복사하는 임시방편) 대신 선택 — 로그인/이체/AI대화가 핵심 기능이라
인스턴스 간 데이터 불일치를 감수할 수 없음(`/tmp`는 콜드스타트마다 리셋되고 인스턴스간 공유 안 됨).

## 조사 결과
- SQLite 문법은 대부분 호환(AUTOINCREMENT, date('unixepoch') 등 — libSQL이 SQL 레벨에서 SQLite와 호환).
- 진짜 이슈 2가지 (libsql-python 공식 문서 확인):
  - `row_factory` 미구현 — `dict(row)`/`row["col"]` 패턴이 db.py 전역(~85개 함수)에서 쓰이지만,
    `get_conn()` 한 곳에 Row 호환 래퍼(cursor.description으로 dict처럼 동작)를 추가하면 우회 가능.
    나머지 함수는 안 건드려도 됨.
  - `executescript()` 미구현 — `init_db()`의 다중 CREATE TABLE 스크립트를 개별 statement로 분리해야 함.
## 1단계 파일럿 테스트 결과 (2026-07-30, 완료)
로컬 libsql 파일 모드(PyPI `libsql` 패키지, Turso 원격과 동일 엔진)로 `/private/tmp/.../scratchpad/pilot_test.py` 실행해 확정.

- **`sqlite3.IntegrityError` catch 3곳 (`backend/app.py:212,812,1396`) — 실제로 깨짐, 수정 필요.**
  UNIQUE 제약 위반 시 libsql은 `sqlite3.IntegrityError`가 아니라 **`ValueError`**(`"UNIQUE constraint failed: ..."`)를 던짐. `isinstance(e, sqlite3.IntegrityError)` → `False`.
  지금 코드 그대로면 계좌 중복등록/이벤트 중복응모 시 의도한 400 대신 처리 안 된 500이 터짐.
  → 전체 마이그레이션 때 3곳 모두 `except (sqlite3.IntegrityError, ValueError):`로 수정 필요.

- **`process_transfer()`의 `conn.isolation_level = None` (`backend/db.py:420`) — 그 자체가 크래시 원인, 삭제로 해결.**
  libsql에서 `isolation_level` 속성은 read-only라 대입 시 `AttributeError: attribute 'isolation_level' of 'builtins.Connection' objects is not writable`. 이 줄이 있는 한 이체 워커가 호출마다 무조건 죽음.
  단, 이 줄을 **삭제**하고 나머지 로직(`BEGIN IMMEDIATE` → 로직 → `COMMIT`/`ROLLBACK`)만 테스트하니 완전 정상 동작 — libsql 기본 `isolation_level`이 이미 `"DEFERRED"`라 명시적 BEGIN/COMMIT/ROLLBACK 패턴과 자연히 호환됨(커밋 후 잔액 반영·롤백 후 미반영 둘 다 확인).
  → 전체 마이그레이션 때 `db.py:420` 한 줄만 삭제, 트랜잭션 로직 자체는 무수정.

- (참고) `row["col"]` dict-style 접근은 예상대로 `TypeError`(row_factory 미구현) — 기존에 파악한 이슈와 일치, Row 래퍼로 해결 예정.

## 2단계 결정 + 구현 (2026-07-30, 완료)
**결정**: 로컬은 지금처럼 sqlite3로 완전 오프라인 유지, Vercel 배포 환경에서만 `TURSO_DATABASE_URL` 환경변수가 설정된 경우 libsql로 분기(사용자 선택 — DB 연결계층에 분기 로직 남는 대신 로컬 개발은 손대지 않음).

**구현 내용** (`backend/db.py`, `backend/app.py`, `requirements.txt`):
- `db.py`에 `_LibsqlRow`/`_LibsqlCursor`/`_LibsqlConnection` 래퍼 3종 추가 — libsql의 `row_factory` 미구현을 우회해 `row["col"]`/`dict(row)`/`with conn:` 트랜잭션 프로토콜을 sqlite3.Row와 동일하게 흉내냄. `get_conn()`이 `TURSO_DATABASE_URL` 유무로 분기(없으면 기존 sqlite3 경로 그대로, 있으면 `import libsql`로 연결 후 래핑). `TURSO_AUTH_TOKEN`은 있을 때만 전달(로컬 파일 테스트처럼 토큰 없는 연결도 지원).
- `init_db()`의 `executescript()` 호출을 `_SCHEMA_SQL.split(";")` + 개별 `execute()` 루프로 교체 — sqlite3/libsql 양쪽에 동일하게 동작해 이 부분은 엔진 분기 불필요.
- `process_transfer()`(`db.py`): `conn.isolation_level = None`을 `isinstance(conn, sqlite3.Connection)`일 때만 실행하도록 가드. libsql은 기본 `isolation_level`이 이미 `DEFERRED`라 뒤따르는 `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK`과 자연히 호환됨(1단계 파일럿에서 확인).
- `app.py`의 `except sqlite3.IntegrityError:` 3곳(계좌등록 2곳 + 이벤트응모 1곳) → `except (sqlite3.IntegrityError, ValueError):`로 확장. 3곳 모두 단순 INSERT 하나만 감싸는 좁은 try 블록이라 ValueError 오탐 위험 낮음(직접 확인).
- `requirements.txt`에 `libsql` 추가.

**검증**: `backend/db.py`는 외부 의존성이 stdlib+libsql뿐이라 실제로 로컬 파일 두 종류(sqlite3 경로 / `TURSO_DATABASE_URL`=로컬 파일 경로로 libsql 경로 흉내)에 대해 `init_db()` → `create_user`/`create_account`(중복 계좌 예외 타입 확인) → `process_transfer()` 성공 경로(잔액 차감 커밋) → 실패 경로(잔액부족 시 ROLLBACK, 잔액 안 바뀜) 전부 실행해 통과 확인함. 로컬 sqlite3 경로는 기존과 동일하게 동작(회귀 없음).

**아직 안 한 것**: 실제 Turso 계정 생성(`turso auth login` 필요) + DB 프로비저닝 + Vercel 프로젝트에 `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` 환경변수 설정 + 배포 후 실환경 검증.

## 3단계 Turso 계정/DB 프로비저닝 + 실서버 검증 (2026-07-30, 완료 — 단, 중요 이슈 발견)
- `turso auth login`(사용자가 브라우저 인증 직접 수행) → `turso db create matchbank` → 그룹 `default`, 리전 `aws-ap-northeast-1`.
- URL: `libsql://matchbank-junga-k.aws-ap-northeast-1.turso.io`, 토큰은 `turso db tokens create matchbank`(만료 없음)로 발급 — **토큰 값 자체는 기록하지 않음**, 필요 시 재발급하거나 `turso db tokens create matchbank`로 재조회.
- 실제 원격 서버(로컬 흉내 아님)에 `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` 붙여서 검증:
  1. **중복 예외**: 원격에서도 `ValueError`(Hrana 프로토콜 래핑 메시지 포함) 발생, `except (sqlite3.IntegrityError, ValueError):`로 정상 캐치 확인.
  2. **`process_transfer` 원자성**: 성공 케이스(커밋 후 잔액 반영)·실패 케이스(잔액부족 → ROLLBACK, 잔액 불변) 둘 다 원격에서 정상 확인.
  3. **지연시간 — ⚠️ 발견된 이슈**: 새 연결 생성(핸드셰이크) 자체가 평균 **~550~600ms**, 이미 연결된 커넥션 재사용 시 쿼리는 **~75ms**. 즉 지연의 대부분은 "연결 생성" 비용이지 쿼리 자체가 아님.
     **문제**: `db.py`는 거의 모든 함수가 `with get_conn() as conn:`으로 **매번 새 연결을 연다**(106곳). 예를 들어 `POST /api/transfer` 하나가 `auth.get_current_user`(1) + `list_accounts`(1) + `lookup_account`(1) + `sum_user_transfers_today`(1) + `get_user_by_username`(1) + `get_transfer_password_hash`(1) 등 **순차적으로 5~6개 별도 연결**을 여는 구조라, 단순 곱셈으로 이 한 요청만 최소 3~4초가 걸릴 수 있음. 로컬 SQLite에서는 연결 비용이 사실상 0이라 이 구조가 문제되지 않았지만, Turso(원격 네트워크)로 옮기면 체감 지연이 커짐.
     **아직 미해결**: 연결 재사용(예: FastAPI request-scoped 커넥션 캐싱, 혹은 커넥션 풀) 없이 지금 상태로 배포하면 다중-DB콜 엔드포인트(로그인 후 인증이 걸린 대부분)가 눈에 띄게 느려질 수 있음.

## 3-1단계 연결 재사용 — 대안 조사 후 구현 + 실측 (2026-07-30, 완료)
연결 재사용 문제(3단계에서 발견)를 어떻게 풀지, app.py 수정 범위가 더 작은 대안이 있는지 먼저 조사:

- **Vercel warm 인스턴스 재사용 검토 → 채택 안 함**: Fluid Compute(현재 기본 실행모델, Python 포함)가 인스턴스를 재사용함을 확인했으나, FastAPI 동기 라우트는 스레드풀에서 병렬 실행되므로 전역 커넥션 하나를 공유하면 트랜잭션 상태가 꼬일 위험 — 스레드-세이프 풀(락+재연결)이 필요해 오히려 범위가 넓어짐.
- **Turso embedded replica 검토 → 탈락**: 실제 원격 DB로 테스트 결과, `.sync()` 호출 전에는 로컬에 쓴 데이터가 원격에 전혀 반영되지 않음을 확인(별도 순수 원격 연결로 조회 시 `None`). 서버리스 다중 인스턴스 구조상 2단계에서 기각한 "/tmp 복사"와 동일한 인스턴스간 불일치 문제라 쓰기 경로(이체)에 쓸 수 없음.
- **→ 채택**: 요청-스코프 연결 공유. `db.py`의 기존 85개 함수 본문은 무수정.
  - `db.py`: `contextvars.ContextVar`(`_request_conn`)에 "현재 요청의 연결"을 담아두고 `get_conn()`이 있으면 재사용·없으면 `_open_conn()`으로 새로 열도록 분리. `request_scope` 컨텍스트매니저 추가.
  - `app.py`: `@app.middleware("http")`로 요청마다 `with db.request_scope():` 감싸기 5줄 추가.
  - FastAPI 동기 라우트가 스레드풀에서 실행돼도 contextvar가 정상 전파됨을 토이 예제로 검증.
- **실측(Kafka도 로컬에 띄워서 `/api/transfer` 실제 엔드포인트를 실제 원격 Turso로 호출)**:
  | | before(연결 재사용 없음) | after(요청-스코프 공유) |
  |---|---|---|
  | `/api/signup` (db 호출 7회) | 4932ms | 2014ms |
  | `/api/transfer` (평균 5회) | 5713ms | **1772ms** |

## 3-2단계 쿼리별 트레이스 + 병렬화 가능성 조사 (2026-07-30, 완료 — 병렬화 불가로 결론)
`/api/transfer` 안의 db 호출 9개를 실제 원격 Turso로 개별 타이밍 측정 (합계 933ms, 요청 전체 1899ms — 나머지는 연결 핸드셰이크+Kafka 발행+직렬화).
- 데이터 의존관계상 `lookup_account`/`get_transfer_password_hash`/재조회 `get_user_by_username` 등 4~5개는 논리적으로 병렬 가능해 보였음.
- **실측으로 확정: 병렬화 불가.** 같은 연결로 스레드 4개 동시 실행 → 내부에서 그냥 직렬화(진짜 동시성 없음). 연결을 4개 따로 열고 스레드로 동시 실행해도 이득 없음(오히려 순차보다 느림) — `libsql` 파이썬 바인딩이 동기 드라이버라 네트워크 I/O 중 GIL을 안 놓는 것으로 보임. 진짜 병렬성을 얻으려면 Turso의 별도 비동기 클라이언트(`libsql_client`)로 갈아타야 하는데 이는 app.py/db.py 전체를 `async def`로 재작성하는 큰 작업이라 범위 밖.
- **결론**: 현재 구조(동기 FastAPI + libsql 동기 드라이버)에서 `/api/transfer` ~1.7~2초는 현실적 하한선. 안전하게 뺄 수 있는 중복 하나(재조회 `get_user_by_username`, ~75~150ms)는 있지만 병렬화는 아님 — 아직 미적용.

## 3-3단계 미들웨어 예외 안전성 검증 + 커넥션 누수 2건 발견·수정 (2026-07-30, 완료)
- **미들웨어(`with db.request_scope():`) 예외 안전성**: 실제 원격 Turso로 실패 요청 20회(400 10회 + 강제 unhandled 예외 10회) 반복 → 매번 정확히 +1open/+1close, 누적 차이 없음. `with` 문 자체가 `__exit__`을 예외 여부와 무관하게 보장하므로 구조적으로 안전함을 확인.
- **그 과정에서 발견한 별개 이슈 — 정확한 원인**: `_LibsqlConnection.__exit__`의 결함이 아니라(의도대로 정확히 동작 — Python `sqlite3.Connection`과 동일하게 `with`는 트랜잭션 commit/rollback만 하고 연결은 안 닫음, 요청-스코프 재사용을 위해 필요한 설계), **`init_db()`와 예약이체 폴러가 `with get_conn() as conn:`은 쓰면서도 "다 쓴 연결을 닫는" 별도 책임을 아무도 안 지고 있었던 것**. 래퍼 결함이 아니므로 나머지 85개 함수 재검증 불필요(요청-스코프 안에서 호출되면 스코프가 닫고, standalone 호출되는 함수는 각자 자체 close 보유).
  - `init_db()`: `conn = get_conn(); try: with conn: ... finally: conn.close()`로 수정 — 호출자가 명시적으로 닫음.
  - 예약이체 폴러(`app.py` `_loop()`): 사이클 전체를 `with db.request_scope():`로 감쌈.
  - `process_transfer()`: `owns_conn = _request_conn.get() is None`로 소유권 판별 → 빌려온 연결이면 자체 close 안 함(스코프 소유자가 닫음), standalone 호출(예: `transfer_consumer.py`)이면 기존처럼 자체 close.
  - 검증: startup 직후 `opened=2,closed=2`(이전엔 opened=3,closed=1). 폴러 3분(13사이클) 관찰 — 매 체크포인트 차이 0, 누적 없음.
- **같은 패턴을 하나 더 발견**: `transfer_consumer.py`(Kafka 컨슈머)의 에러 경로 `db.fail_transfer(...)`도 standalone `with get_conn()`이라 동일 문제. 메시지 처리 전체를 `with db.request_scope():`로 감싸도록 수정.
  - 검증: 실제 Kafka+실제 원격 Turso로 `process_transfer`를 강제 예외 발생시켜 에러 경로 22회 반복 처리 → 전 구간(2/2~32/32) 차이 0.
- **코드베이스 전체에서 상시 실행 루프는 이 둘(폴러·컨슈머)뿐임을 확인**(`threading.Thread`/`while True` grep) — "개별 호출부 책임 누락" 패턴에 대한 원인 규명과 수정은 이걸로 완료.

## 4단계 커밋 분리 + main 병합 + 실배포 (2026-07-30, 완료)
- 워크트리(`worktree-vercel-deploy`)의 미커밋 변경사항을 작업 단위 4개 커밋으로 분리(`87fb984` Turso 마이그레이션 핵심/db.py, `a50ad6f` 요청-스코프 연결 재사용 미들웨어, `44d2cfc` 폴러·컨슈머 커넥션 누수 수정, `a3c39ef` 진행기록 문서화). app.py는 hunk 단위로 정확히 분리(`git apply --cached`), db.py는 각 단계가 물리적으로 얽혀있어 하나의 커밋으로 유지(커밋 메시지에 명시).
- `main`으로 fast-forward 머지 → 워크트리 제거(`git worktree remove`) → 브랜치 삭제(`git branch -d`) → main 기준 git status 확인(오늘 작업분은 전부 클린, 단 무관한 `backlog.md`/`session-log.md`가 이미 수정된 상태였음 — 손대지 않음).
- Vercel 프로젝트(`bankservice`, 기존에 크래시났던 그 프로젝트) 연결 → `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN`을 Production+Preview 환경변수로 등록 → `vercel --prod` 배포.
- **첫 배포 실패 → 원인 발견·수정**: `ModuleNotFoundError: No module named 'libsql'`. Vercel Python 빌드는 `requirements.txt`가 아니라 **`pyproject.toml`의 `dependencies`**를 씀(빌드로그: "Installing required dependencies from pyproject.toml..."). `libsql`을 `requirements.txt`에만 추가했던 게 원인 — `pyproject.toml`에도 추가(커밋 `3bf0096`) 후 재배포해 해결.
- **재배포 후 실 프로덕션 URL로 스모크 테스트**(`https://bankservice-six.vercel.app`):
  - 사이트 루트 200, `/api/login` 401(정상 — 이 Turso DB엔 데모 계정 시드 안 함)이지만 응답 자체가 왔다는 게 Turso 연결 정상 동작 확인. 콜드스타트 첫 요청 14.5s → 웜 상태 1.3~1.5s(3단계 로컬 실측치 1.7~2s와 거의 일치, 리전 불일치 우려는 기우로 확인됨).
  - `/api/products`(FSS 외부 API, Turso 무관) 웜 상태 200 정상.
  - **실제 회원가입(A/B) → 로그인 → 이체 전체 흐름**을 프로덕션 URL로 직접 실행: 회원가입/로그인/Turso 잔액 반영까지는 전부 정상. **`/api/transfer`에서 Kafka 발행이 걸림** — 로그 확인 결과 `kafka-python`이 `localhost:9092`(로컬 개발용 주소)에 지수 백오프로 재시도하다 20초 넘게 걸려서야 `KafkaTimeoutError`로 실패(→ 이체 status=`failed`로 정상 처리는 됨, 단 응답이 수십 초 걸림). **오늘 마이그레이션 범위(Turso) 밖의 별개 이슈** — Vercel엔 애초에 Kafka가 없고 `KAFKA_BROKER` 환경변수로 외부 Kafka를 지정한 적도 없음. 테스트로 만든 사용자·계좌·이체 데이터는 정리 완료.

**결론**: Turso 마이그레이션 자체(로그인/회원가입/상품조회/DB 읽고쓰기)는 프로덕션에서 정상 동작 확인. `/api/transfer`의 즉시실행 경로는 Kafka 브로커를 어딘가에 배포하고 `KAFKA_BROKER` 환경변수로 연결하기 전까지는 느리게(수십 초) 실패함 — 별도 후속 작업 필요.

## 5단계 Kafka 미가용 시 이체 차단 문제 진단·수정 (2026-07-31, 완료)
- **확인**: Kafka 발행 실패가 부가기능(알림/로깅) 수준이 아니라 **이체 자체를 완전히 막는 core path**임을 코드로 확인. `process_transfer()`(실제 잔액차감+거래내역기록)는 오직 Kafka 컨슈머(`transfer_consumer.py`)를 통해서만 호출되고, `/api/transfer`는 그 함수를 직접 안 부름 — Kafka 없으면 `transfers` 행만 생기고 아무도 처리를 안 해서 `failed`로 끝나거나 무한 대기.
- **수정**(`backend/app.py` `transfer()`, `backend/kafka_io.py`, 커밋 `91de157`):
  - Kafka 발행 실패(or `KAFKA_DISABLED=true`) 시 같은 요청 안에서 `db.process_transfer()`를 동기 실행하는 폴백 추가. `process_transfer()`의 기존 멱등성 가드(`status != 'pending'`이면 스킵) 덕분에 나중에 Kafka가 살아나 같은 메시지를 재처리해도 이중 차감 없음. `fail_transfer()`를 먼저 호출하면 이 가드에 걸려 동기 처리가 스킵되므로 호출 순서 주의.
  - `kafka_io.make_producer()`에 `bootstrap_timeout_ms=3000` 추가(기본 30000ms가 원인 중 하나) — 그런데 실측해보니 kafka-python이 부트스트랩 사이클을 여러 번 반복 재시도해서(백오프 구간은 이 타임아웃에 안 걸림) 여전히 20초 이상 걸림 → **`KAFKA_DISABLED` 환경변수**로 부트스트랩 시도 자체를 건너뛰는 스위치 추가, Vercel Production+Preview에 `true`로 설정.
- **재측정 중 진짜 원인 추가 발견**: `KAFKA_DISABLED` 적용 후에도 웜 상태 응답이 9~11초로 예상(3초+933ms대)보다 훨씬 큼. 프로덕션에 임시 타이밍 로그를 심어(진단 후 제거) 확인한 결과, **Vercel(iad1, 미국 동부) ↔ Turso(aws-ap-northeast-1, 아시아) 간 쿼리 1회당 실제 300~650ms**(로컬 실측치 75~170ms의 3~4배) — 리전 거리 차이가 진짜 원인. `process_transfer()` 자체가 내부 순차쿼리 8~10개라 그것만 4~5초.
- 실제 원격 Turso로 정확성 재검증: 프로덕션 URL로 회원가입→로그인→이체 실행 후 **DB 직접 조회로 잔액 변화·거래내역 확인**(A -30,000원/B +30,000원 정확히 반영). 테스트 데이터 정리 완료.

## 6단계 process_transfer() 쿼리 병합으로 리전 지연 일부 완화 (2026-07-31, 완료)
- Kafka 브로커+컨슈머 상시 호스팅(Railway/Render/Fly.io/Confluent Cloud 비교 조사 포함)은 오늘 범위 밖 → `backlog.md`로 이관.
- `process_transfer()`의 읽기 쿼리 3개(이체정보/출금계좌/입금계좌, 전부 `transfers.id` 하나에서 시작하지만 순차 SELECT였음)를 `LEFT JOIN` 하나로 병합(커밋 `83f1400`). 왕복 11회(수수료 있는 내부이체 기준)→9회. 쓰기 쿼리(UPDATE/INSERT)는 서로 다른 행을 건드리는 개별 DML이라 병합 불가, libsql이 `executescript()`도 미구현이라 배치도 안 됨 — `backlog.md`에 별도 기록.
- 실제 원격 Turso로 정확성 검증: 정상(수수료 포함)/잔액부족(롤백)/외부계좌(LEFT JOIN dst NULL) 3가지 케이스 전부 정확한 잔액·상태 확인 후 배포.
- **재측정(프로덕션, 실제 성공 이체 기준)**: 웜 상태 평균 이전 ~10.2초(9.35/10.80/10.63s) → 이후 ~9.0초(9.48/8.35/9.19s), 약 1.2초(~12%) 절감 — 애초 예상한 600~1300ms 절감과 일치. (측정 중 손으로 옮겨적은 JWT 토큰이 한 번 손상돼 "1초대" 오측정이 있었으나, 응답 본문 확인으로 인증 실패임을 발견해 토큰을 파일로 안전하게 재발급받아 재측정 — 실제 이체 성공 케이스만 반영된 수치가 위 값).

## 다음 단계
1. ~~파일럿 테스트(IntegrityError, 수동 트랜잭션)~~ 완료
2. ~~로컬 개발 환경 전략 결정~~ 완료
3. ~~Turso 계정/DB 프로비저닝 + 실서버 검증~~ 완료
4. ~~연결 재사용 문제 해결~~ 완료 — 요청-스코프 공유 구현 + 실측(3~5배 개선)
5. ~~쿼리 병렬화 가능성 조사~~ 완료 — 현재 스택에서 불가
6. ~~미들웨어 예외 안전성 검증 + 커넥션 누수 2건(init_db·폴러, 컨슈머) 수정·검증~~ 완료
7. ~~커밋 분리 + main 병합 + Vercel 환경변수 설정 + 실배포~~ 완료
8. ~~Kafka 미가용 시 이체 차단 문제 진단·수정~~ 완료 — 동기 폴백 + `KAFKA_DISABLED`
9. ~~process_transfer() 읽기쿼리 JOIN 병합~~ 완료 — 웜 상태 ~10.2s → ~9.0s
10. (후속, `backlog.md` 참고) Kafka 브로커+컨슈머 상시 호스팅, process_transfer() 쓰기쿼리 배치

## 참고
- Turso CLI는 로컬에 설치돼 있으나 로그인 필요 (`turso auth login`, 브라우저 인증 필요) — 완료, DB `matchbank` 생성됨(`libsql://matchbank-junga-k.aws-ap-northeast-1.turso.io`).
- 프로덕션 URL: `https://bankservice-six.vercel.app` (Vercel 프로젝트 `junga-k/bankservice`).
