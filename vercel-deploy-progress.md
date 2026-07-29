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
- 미확정 리스크 2가지 (실제 코드로 테스트해봐야 확정됨):
  - `backend/app.py`의 `sqlite3.IntegrityError` catch 3곳 — libsql이 같은 예외 클래스를 던지는지 미확인.
  - `process_transfer()`의 수동 트랜잭션(`isolation_level=None` + `BEGIN IMMEDIATE`/`COMMIT`) 호환 여부.

## 다음 단계
1. 위험도 높은 두 항목(IntegrityError 예외 타입, 수동 트랜잭션) 파일럿 테스트
2. 로컬 개발 환경 전략 결정 (로컬도 Turso에 붙일지, 로컬은 SQLite 유지하고 분기할지)
3. 전체 마이그레이션 (db.py 연결부 교체 + Row 래퍼 + init_db 재작성 + app.py 3곳 수정 + 의존성 추가 + Vercel 환경변수 설정)

## 참고
- Turso CLI는 로컬에 설치돼 있으나 로그인 필요 (`turso auth login`, 브라우저 인증 필요).
