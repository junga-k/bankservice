"""은행 데모 데이터 시드.

내 계좌(데모 사용자 '홍길동') 3개 + 과거 거래내역,
그리고 이체 받는 사람으로 쓸 다른 사용자 계좌 몇 개를 넣는다.
이미 시드되어 있으면 건너뛴다(멱등). 실행: .venv/bin/python seed_bank.py

⚠️ 전부 가짜 데이터다(실제 계좌·돈·개인정보 아님).
"""
from __future__ import annotations

import os
import time

from backend import auth, db

DEMO_USER = "demo"
DEMO_PASSWORD = "demo1234"  # 관리자가 방문자 기능(계좌조회·이체·은행원 등) 테스트에 쓰는 고정 비밀번호
DEMO_TRANSFER_PIN = "246810"  # 데모 이체 비밀번호(숫자 6자리)
DEMO_HOLDER = "홍길동"

# 관리자 계정 — 개발자 전용이다. 공개하지 않는다.
# 라이브 비밀번호는 ADMIN_PASSWORD 환경변수로 준다. 미설정 시 로컬 개발용 폴백.
# ⚠️ 이미 존재하는 사용자의 비밀번호는 아래 _ensure_admin 이 고치지 않는다(멱등 보장).
#    따라서 라이브에서 이 값을 바꾸려면 admin 으로 로그인해 마이페이지>보안에서 한 번 변경해야 한다.
ADMIN_USER = "admin"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip() or "admin1234"
ADMIN_TRANSFER_PIN = "135790"  # 관리자 이체 비밀번호(숫자 6자리)
ADMIN_HOLDER = "관리자"

# 방문자(채용담당자 등)에게 안내하는 공개 계정.
# 로그인 화면이 이 아이디를 인쇄하고 비밀번호를 자동 입력한다(backend/app.py 의 env-config.js
# → site/js/main.js). admin 을 공개하지 않는 이유는 둘이다:
#   (1) 실제 관리자 아이디가 밖으로 나가지 않는다 — /api/reset-password 의 표적이 되지 않게.
#   (2) 개발자가 확인용으로 쓰는 계정과 방문자 계정이 잔액·일일이체한도·거래내역·접근로그를
#       공유하지 않는다(DB 는 per-session 격리가 없는 전역 공유 상태다).
# 백오피스를 보여주려면 role 이 'admin' 이어야 한다(auth.require_admin).
# 실질 쓰기 방어는 DEMO_READONLY 미들웨어가 담당한다.
REVIEWER_USER = "reviewer"
REVIEWER_PASSWORD = os.environ.get("REVIEWER_PASSWORD", "").strip() or "reviewer1234"
REVIEWER_TRANSFER_PIN = (os.environ.get("REVIEWER_TRANSFER_PIN", "").strip()
                         or "802413")  # demo(246810)·admin(135790) 과 다른 값
REVIEWER_HOLDER = "김서연"


def _ensure_transfer_pin(conn, user_id: int, pin: str) -> None:
    """transfer_password_hash가 비어있으면 채운다(멱등)."""
    row = conn.execute(
        "SELECT transfer_password_hash FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is not None and not (row["transfer_password_hash"] or "").strip():
        conn.execute(
            "UPDATE users SET transfer_password_hash = ? WHERE id = ?",
            (auth.hash_password(pin), user_id),
        )

# 관리자 본인 계좌: 관리자도 '내 계좌' 화면(조회·이체)을 온전히 볼 수 있도록 시드
ADMIN_ACCOUNTS = [
    ("111-222-333444", "신한은행", 5_000_000),
    ("777-88-9990001", "카카오뱅크", 1_250_000),
]
ADMIN_PAST_TX = {
    "111-222-333444": [
        ("in", 4_000_000, "급여", 18),
        ("out", 320_000, "카드대금", 6),
    ],
    "777-88-9990001": [
        ("in", 500_000, "이체", 10),
        ("out", 78_000, "쇼핑", 2),
    ],
}

# 방문자 공개 계정의 본인 계좌.
# 계좌를 2개 이상 두는 이유: AI 은행원 이체 확인 카드의 '출금 계좌' selectbox 는
# len(_accts) > 1 일 때만 나타난다(app.py). 1개면 그 UI 를 볼 수 없다.
REVIEWER_ACCOUNTS = [
    ("352-0101-2345-67", "농협은행", 4_300_000),
    ("1000-99-887766", "케이뱅크", 920_000),
]
REVIEWER_PAST_TX = {
    "352-0101-2345-67": [
        ("in", 3_800_000, "급여", 16),
        ("out", 1_200_000, "월세", 9),
        ("out", 68_000, "통신요금", 4),
    ],
    "1000-99-887766": [
        ("in", 700_000, "이체", 11),
        ("out", 35_000, "구독료", 2),
    ],
}

# 내 계좌: (account_no, bank_name, balance)
MY_ACCOUNTS = [
    ("110-123-456789", "신한은행", 1_500_000),
    ("004-21-0987654", "국민은행", 3_200_000),
    ("3333-01-1234567", "카카오뱅크", 850_000),
]

# 받는 사람 계좌(예금주 조회·이체 대상): (username, holder, account_no, bank, balance)
OTHER_ACCOUNTS = [
    ("chulsoo", "김철수", "1002-333-444555", "우리은행", 500_000),
    ("younghee", "이영희", "218-910111-12345", "하나은행", 700_000),
    ("minsoo", "박민수", "100-2345-6789", "토스뱅크", 300_000),
]

# 내 계좌별 과거 거래내역 (type, amount, counterparty, 며칠 전)
PAST_TX = {
    "110-123-456789": [
        ("in", 2_000_000, "급여", 20),
        ("out", 55_000, "통신요금", 12),
        ("out", 130_000, "카드대금", 5),
    ],
    "004-21-0987654": [
        ("in", 3_000_000, "보너스", 30),
        ("out", 200_000, "관리비", 8),
    ],
    "3333-01-1234567": [
        ("in", 1_000_000, "이체", 15),
        ("out", 42_000, "쇼핑", 3),
    ],
}


def main() -> None:
    db.init_db()
    with db.get_conn() as conn:
        now = time.time()

        # 이미 시드됨? (기존 bank.db는 demo의 password_hash/name이 비어있거나 admin이 없을 수 있으므로 보정)
        demo_row = conn.execute(
            "SELECT id, password_hash, name FROM users WHERE username = ?", (DEMO_USER,)
        ).fetchone()
        if demo_row:
            if not demo_row["password_hash"]:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (auth.hash_password(DEMO_PASSWORD), demo_row["id"]),
                )
                print(f"'{DEMO_USER}' 계정 비밀번호 백필 완료")
            if not demo_row["name"]:
                conn.execute(
                    "UPDATE users SET name = ? WHERE id = ?", (DEMO_HOLDER, demo_row["id"]),
                )
            _ensure_transfer_pin(conn, demo_row["id"], DEMO_TRANSFER_PIN)
            _ensure_admin(conn, now)
            _ensure_reviewer(conn, now)
            _seed_runtime_rows(conn, now)
            print(f"이미 시드됨 (user '{DEMO_USER}' 존재) — "
                  f"이체PIN/관리자/공개계정/런타임행 보정 후 건너뜀")
            return

        # ── 데모 사용자 + 내 계좌 ──
        conn.execute(
            "INSERT INTO users(id, username, password_hash, name, created_at) VALUES (?, ?, ?, ?, ?)",
            (db.DEMO_USER_ID, DEMO_USER, auth.hash_password(DEMO_PASSWORD), DEMO_HOLDER, now),
        )
        _ensure_transfer_pin(conn, db.DEMO_USER_ID, DEMO_TRANSFER_PIN)
        for account_no, bank, balance in MY_ACCOUNTS:
            cur = conn.execute(
                "INSERT INTO accounts(user_id, account_no, bank_name, holder_name, balance) "
                "VALUES (?, ?, ?, ?, ?)",
                (db.DEMO_USER_ID, account_no, bank, DEMO_HOLDER, balance),
            )
            acc_id = cur.lastrowid
            for typ, amount, counterparty, days_ago in PAST_TX.get(account_no, []):
                conn.execute(
                    "INSERT INTO transactions(account_id, type, amount, counterparty, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (acc_id, typ, amount, counterparty, now - days_ago * 86400),
                )

        # ── 받는 사람 계좌 ──
        for username, holder, account_no, bank, balance in OTHER_ACCOUNTS:
            cur = conn.execute(
                "INSERT INTO users(username, password_hash, created_at) VALUES (?, '', ?)",
                (username, now),
            )
            conn.execute(
                "INSERT INTO accounts(user_id, account_no, bank_name, holder_name, balance) "
                "VALUES (?, ?, ?, ?, ?)",
                (cur.lastrowid, account_no, bank, holder, balance),
            )

        # ── 관리자 계정(개발자 전용) + 공개 계정(방문자용) ──
        _ensure_admin(conn, now)
        _ensure_reviewer(conn, now)

        # ── 런타임 누적 테이블 시드(백오피스·마이페이지가 비어 보이지 않게) ──
        # event_entries 는 seed_support.py 가 이벤트를 만든 뒤에야 채워진다.
        # seed_support.py 를 나중에 돌리면 이 스크립트를 한 번 더 실행하면 된다(멱등).
        runtime = _seed_runtime_rows(conn, now)

    print(f"시드 완료: 내 계좌 {len(MY_ACCOUNTS)}개(홍길동), "
          f"받는 계좌 {len(OTHER_ACCOUNTS)}개, "
          f"관리자 '{ADMIN_USER}', 공개 계정 '{REVIEWER_USER}', "
          f"런타임 행 {runtime}")


def _ensure_admin(conn, now: float) -> None:
    """admin 계정과 그 본인 계좌를 보장한다(멱등)."""
    row = conn.execute("SELECT id FROM users WHERE username = ?", (ADMIN_USER,)).fetchone()
    if row:
        admin_id = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, name, role, created_at) "
            "VALUES (?, ?, ?, 'admin', ?)",
            (ADMIN_USER, auth.hash_password(ADMIN_PASSWORD), "관리자", now),
        )
        admin_id = cur.lastrowid
        print(f"'{ADMIN_USER}' 관리자 계정 생성 완료")

    _ensure_transfer_pin(conn, admin_id, ADMIN_TRANSFER_PIN)

    # 관리자도 '내 계좌' 화면을 온전히 볼 수 있도록 계좌가 없으면 시드
    has_acct = conn.execute(
        "SELECT COUNT(*) AS n FROM accounts WHERE user_id = ?", (admin_id,)
    ).fetchone()["n"]
    if not has_acct:
        for account_no, bank, balance in ADMIN_ACCOUNTS:
            cur = conn.execute(
                "INSERT INTO accounts(user_id, account_no, bank_name, holder_name, balance) "
                "VALUES (?, ?, ?, ?, ?)",
                (admin_id, account_no, bank, ADMIN_HOLDER, balance),
            )
            acc_id = cur.lastrowid
            for typ, amount, counterparty, days_ago in ADMIN_PAST_TX.get(account_no, []):
                conn.execute(
                    "INSERT INTO transactions(account_id, type, amount, counterparty, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (acc_id, typ, amount, counterparty, now - days_ago * 86400),
                )
        print(f"'{ADMIN_USER}' 관리자 계좌 {len(ADMIN_ACCOUNTS)}개 시드 완료")


def _ensure_reviewer(conn, now: float) -> None:
    """방문자 공개 계정(reviewer)과 그 본인 계좌를 보장한다(멱등).

    _ensure_admin 과 같은 패턴이다. 기존 사용자의 비밀번호는 고치지 않는다 —
    라이브에서 값을 바꾸려면 마이페이지>보안에서 한 번 변경해야 한다.
    """
    row = conn.execute(
        "SELECT id FROM users WHERE username = ?", (REVIEWER_USER,)
    ).fetchone()
    if row:
        reviewer_id = row["id"]
    else:
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, name, role, created_at) "
            "VALUES (?, ?, ?, 'admin', ?)",
            (REVIEWER_USER, auth.hash_password(REVIEWER_PASSWORD), REVIEWER_HOLDER, now),
        )
        reviewer_id = cur.lastrowid
        print(f"'{REVIEWER_USER}' 공개 계정 생성 완료")

    _ensure_transfer_pin(conn, reviewer_id, REVIEWER_TRANSFER_PIN)

    has_acct = conn.execute(
        "SELECT COUNT(*) AS n FROM accounts WHERE user_id = ?", (reviewer_id,)
    ).fetchone()["n"]
    if not has_acct:
        for account_no, bank, balance in REVIEWER_ACCOUNTS:
            cur = conn.execute(
                "INSERT INTO accounts(user_id, account_no, bank_name, holder_name, balance) "
                "VALUES (?, ?, ?, ?, ?)",
                (reviewer_id, account_no, bank, REVIEWER_HOLDER, balance),
            )
            acc_id = cur.lastrowid
            for typ, amount, counterparty, days_ago in REVIEWER_PAST_TX.get(account_no, []):
                conn.execute(
                    "INSERT INTO transactions(account_id, type, amount, counterparty, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (acc_id, typ, amount, counterparty, now - days_ago * 86400),
                )
        print(f"'{REVIEWER_USER}' 계좌 {len(REVIEWER_ACCOUNTS)}개 시드 완료")


# ── 공개 데모 잔액 리셋 ──────────────────────────────────────────────
# 배포된 데모는 계정을 여럿이 공유하므로, 방문자가 이체할 때마다 잔액이 줄어들고
# 언젠가 잔액 부족으로 이체가 실패한다. 아래 함수가 시드 직후 상태로 되돌린다.
# 위의 시드 상수(MY_ACCOUNTS / ADMIN_ACCOUNTS / OTHER_ACCOUNTS / *_PAST_TX)를 그대로
# 재사용하므로 시드 값을 바꾸면 리셋 기준도 자동으로 따라온다(값이 두 군데로 갈리지 않음).

# 시드로 만들어지는 계정 = 리셋 후에도 남아야 하는 계정. 나머지는 방문자가 가입한 것.
SEED_USERNAMES = (DEMO_USER, ADMIN_USER, REVIEWER_USER, *(u for u, *_ in OTHER_ACCOUNTS))

# 개인신용정보 접근 로그는 전부 지우지 않고 최근 이만큼만 남긴다.
ADMIN_LOG_KEEP = 200


def _seeded_accounts():
    """(계좌번호, 시드 잔액, 시드 거래내역) 목록."""
    for account_no, _bank, balance in MY_ACCOUNTS:
        yield account_no, balance, PAST_TX.get(account_no, [])
    for account_no, _bank, balance in ADMIN_ACCOUNTS:
        yield account_no, balance, ADMIN_PAST_TX.get(account_no, [])
    for account_no, _bank, balance in REVIEWER_ACCOUNTS:
        yield account_no, balance, REVIEWER_PAST_TX.get(account_no, [])
    for _username, _holder, account_no, _bank, balance in OTHER_ACCOUNTS:
        yield account_no, balance, []


# ── 런타임 누적 테이블의 시드 ────────────────────────────────────────
# transfers·security_events·inquiries·favorites·event_entries 는 원래 어떤 시드
# 스크립트도 만들지 않았고, reset_demo_data() 가 전부 DELETE 했다. 그래서 리셋 직후
# (매일 03:00 KST, 방문자 활동 전)에는 아래 화면이 전부 비어 보였다:
#   백오피스 대시보드 이체 KPI·추세 / 이체모니터링 / 이상행동 기록 / 문의내역 /
#   이벤트 응모자, 마이페이지 보안·즐겨찾기·예약이체·문의내역.
# 아침에 들어온 방문자가 "미완성 기능"으로 읽는다. 그래서 리셋을 '삭제'가 아니라
# '시드 상태 복원'으로 만든다 — 거래내역이 이미 쓰는 패턴(DELETE 후 재삽입)과 같다.

# 이체: (from_account, to_account, to_bank, to_holder, amount, status, days_ago, sched_days)
# · 여러 사용자에게 섞는다 — 이체모니터링·대시보드는 전역 화면이라 한 사람 것만 있으면 부자연스럽다.
# · status 를 섞어야 대시보드 상태별 순위와 이체모니터링 상태 필터가 의미를 갖는다.
# · days_ago 를 흩어야 대시보드 스파크라인(발생일별 집계)이 그려진다.
# · sched_days 는 'scheduled' 행의 실행 시점. 며칠 뒤로 두는 이유: 예약 폴러가 15초마다
#   due 건을 실제로 처리하므로(backend/app.py), 리셋 주기(1일) 안에 실행되지 않게 해서
#   시드 이체가 잔액을 건드리지 않도록 한다. 취소 가능 상태는 그대로 보인다.
SEED_TRANSFERS = [
    # reviewer(공개 계정) — 마이페이지 예약이체까지 채운다
    ("352-0101-2345-67", "1002-333-444555", "우리은행", "김철수", 150_000, "completed", 6, None),
    ("352-0101-2345-67", "218-910111-12345", "하나은행", "이영희", 80_000, "completed", 3, None),
    # 오늘 날짜에도 완료 건이 있어야 한다. 없으면 대시보드 '이체 완료금액' 추세가
    # 전일 대비 -100% 로 잡혀 첫 화면에 빨간 하락 배지가 뜬다(실측 확인).
    ("1000-99-887766", "1002-333-444555", "우리은행", "김철수", 130_000, "completed", 0, None),
    ("1000-99-887766", "100-2345-6789", "토스뱅크", "박민수", 45_000, "failed", 2, None),
    ("352-0101-2345-67", "1002-333-444555", "우리은행", "김철수", 200_000, "scheduled", 0, 3),
    # demo(일반 사용자 체험 계정)
    ("110-123-456789", "1002-333-444555", "우리은행", "김철수", 320_000, "completed", 7, None),
    ("004-21-0987654", "100-2345-6789", "토스뱅크", "박민수", 120_000, "completed", 5, None),
    ("3333-01-1234567", "218-910111-12345", "하나은행", "이영희", 60_000, "completed", 4, None),
    ("110-123-456789", "218-910111-12345", "하나은행", "이영희", 95_000, "completed", 2, None),
    ("004-21-0987654", "1002-333-444555", "우리은행", "김철수", 5_500_000, "failed", 1, None),
    ("110-123-456789", "100-2345-6789", "토스뱅크", "박민수", 70_000, "scheduled", 0, 5),
    # admin(개발자 계정)
    ("111-222-333444", "1002-333-444555", "우리은행", "김철수", 250_000, "completed", 8, None),
    ("777-88-9990001", "218-910111-12345", "하나은행", "이영희", 40_000, "completed", 1, None),
]
_FAILED_ERROR = "잔액이 부족하거나 한도를 초과했습니다."

# 이상행동 기록: (event_type, username, from_account, to_account, amount, detail, days_ago)
# event_type 은 스키마 주석의 4종(backend/db.py): password_fail|limit_once|limit_daily|new_payee
SEED_SECURITY_EVENTS = [
    ("new_payee", REVIEWER_USER, "352-0101-2345-67", "1002-333-444555", 150_000,
     "처음 보내는 수취계좌", 6),
    ("password_fail", REVIEWER_USER, "352-0101-2345-67", "100-2345-6789", 45_000,
     "이체 비밀번호 불일치", 2),
    ("limit_once", DEMO_USER, "004-21-0987654", "1002-333-444555", 5_500_000,
     "1회 이체 한도 초과", 1),
    ("new_payee", DEMO_USER, "110-123-456789", "218-910111-12345", 95_000,
     "처음 보내는 수취계좌", 2),
    ("limit_daily", DEMO_USER, "110-123-456789", "100-2345-6789", 4_000_000,
     "1일 누적 이체 한도 초과", 4),
]

# 1:1 문의: (username, title, content, days_ago)
SEED_INQUIRIES = [
    (REVIEWER_USER, "예약이체를 취소할 수 있나요?",
     "며칠 뒤로 걸어둔 예약이체를 실행 전에 취소하고 싶습니다. 어디서 하면 되나요?", 2),
    (REVIEWER_USER, "AI 은행원이 추천한 상품의 기준을 알고 싶습니다",
     "대출 상품을 추천받았는데 어떤 기준으로 고른 것인지 알려주실 수 있을까요?", 5),
    (DEMO_USER, "이체 수수료가 면제되는 조건이 있나요?",
     "타행 이체할 때마다 수수료가 붙는데 면제 조건이 있는지 알고 싶습니다.", 3),
    (DEMO_USER, "타행 이체는 얼마나 걸리나요?",
     "다른 은행으로 보낸 이체가 상대방 계좌에 반영되는 시간이 궁금합니다.", 8),
]

# 즐겨찾기: (username, bank_name, account_no, holder_name, nickname, days_ago)
SEED_FAVORITES = [
    (REVIEWER_USER, "우리은행", "1002-333-444555", "김철수", "김철수(회비)", 6),
    (REVIEWER_USER, "하나은행", "218-910111-12345", "이영희", "이영희", 3),
    (DEMO_USER, "우리은행", "1002-333-444555", "김철수", "김철수", 7),
    (DEMO_USER, "토스뱅크", "100-2345-6789", "박민수", "박민수(월세)", 5),
]

# 이벤트 응모: (username, is_winner). 이벤트 id 는 seed_support.py 가 만든 것을 조회해 쓴다
# (여기서 하드코딩하면 두 스크립트의 실행 순서에 묶인다).
SEED_EVENT_ENTRIES = [
    (REVIEWER_USER, 0),
    (DEMO_USER, 1),
    ("chulsoo", 0),
    ("younghee", 1),
    ("minsoo", 0),
]


def _uid(conn, username: str):
    """username → user_id. 없으면 None."""
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    return row["id"] if row else None


def _seed_runtime_rows(conn, now: float, force: bool = False) -> dict:
    """런타임 누적 테이블에 시드 행을 넣는다.

    force=False(최초 시드 경로)면 이미 행이 있는 테이블은 건드리지 않는다.
    force=True(리셋 경로)면 호출 측이 이미 DELETE 했다고 보고 그대로 넣는다.
    """
    counts = {}

    def _empty(table: str) -> bool:
        if force:
            return True
        n = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        return n == 0

    # ── transfers ──
    if _empty("transfers"):
        n = 0
        for (frm, to, bank, holder, amount, status, days_ago, sched_days) in SEED_TRANSFERS:
            conn.execute(
                "INSERT INTO transfers(from_account, to_account, to_bank, to_holder, amount, "
                "fee, memo, status, error, created_at, scheduled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (frm, to, bank, holder, amount, 0, "", status,
                 _FAILED_ERROR if status == "failed" else None,
                 now - days_ago * 86400,
                 (now + sched_days * 86400) if sched_days else None),
            )
            n += 1
        counts["transfers"] = n

    # ── security_events ──
    if _empty("security_events"):
        n = 0
        for (etype, username, frm, to, amount, detail, days_ago) in SEED_SECURITY_EVENTS:
            conn.execute(
                "INSERT INTO security_events(event_type, username, from_account, to_account, "
                "amount, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (etype, username, frm, to, amount, detail, now - days_ago * 86400),
            )
            n += 1
        counts["security_events"] = n

    # ── inquiries ──
    if _empty("inquiries"):
        n = 0
        for (username, title, content, days_ago) in SEED_INQUIRIES:
            uid = _uid(conn, username)
            if uid is None:
                continue
            conn.execute(
                "INSERT INTO inquiries(user_id, title, content, created_at) VALUES (?, ?, ?, ?)",
                (uid, title, content, now - days_ago * 86400),
            )
            n += 1
        counts["inquiries"] = n

    # ── favorites ──
    if _empty("favorites"):
        n = 0
        for (username, bank, account_no, holder, nickname, days_ago) in SEED_FAVORITES:
            uid = _uid(conn, username)
            if uid is None:
                continue
            conn.execute(
                "INSERT INTO favorites(user_id, bank_name, account_no, holder_name, nickname, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, bank, account_no, holder, nickname, now - days_ago * 86400),
            )
            n += 1
        counts["favorites"] = n

    # ── event_entries ── seed_support.py 가 이벤트를 만든 뒤여야 채워진다
    if _empty("event_entries"):
        n = 0
        ev = conn.execute("SELECT id FROM events ORDER BY id LIMIT 1").fetchone()
        if ev is not None:
            for (username, is_winner) in SEED_EVENT_ENTRIES:
                uid = _uid(conn, username)
                if uid is None:
                    continue
                try:
                    conn.execute(
                        "INSERT INTO event_entries(event_id, user_id, is_winner, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (ev["id"], uid, is_winner, now - 4 * 86400),
                    )
                    n += 1
                except Exception:
                    pass  # UNIQUE(event_id, user_id) — 이미 응모됨
        counts["event_entries"] = n

    return counts


def reset_demo_data() -> dict:
    """데모 데이터를 시드 직후 상태로 되돌린다.

    잔액·거래내역 복원 + 방문자 가입 계정 정리 + 런타임 누적 테이블(이체·이상행동·
    문의·즐겨찾기·이벤트 응모) 복원까지 한다. '삭제'가 아니라 '복원'인 것이 중요하다 —
    지우기만 하면 리셋 직후 백오피스·마이페이지의 여러 패널이 비어 보인다.

    거래내역을 지우고 다시 넣는 이유: 잔액만 되돌리면 기존 거래내역 행의
    balance_after(거래 후 잔액)가 실제 잔액과 어긋나 화면에 이상하게 보인다.
    """
    now = time.time()
    restored, tx_removed = [], 0

    with db.get_conn() as conn:
        for account_no, balance, past_tx in _seeded_accounts():
            row = conn.execute(
                "SELECT id, balance FROM accounts WHERE account_no = ?", (account_no,)
            ).fetchone()
            if row is None:
                continue   # 아직 시드 전이면 건너뜀
            acc_id, before = row["id"], row["balance"]

            # rowcount 는 libsql 에서 신뢰할 수 없어 COUNT 로 센다
            tx_removed += conn.execute(
                "SELECT COUNT(*) AS n FROM transactions WHERE account_id = ?", (acc_id,)
            ).fetchone()["n"]
            conn.execute("DELETE FROM transactions WHERE account_id = ?", (acc_id,))

            for typ, amount, counterparty, days_ago in past_tx:
                conn.execute(
                    "INSERT INTO transactions(account_id, type, amount, counterparty, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (acc_id, typ, amount, counterparty, now - days_ago * 86400),
                )
            conn.execute("UPDATE accounts SET balance = ? WHERE id = ?", (balance, acc_id))
            if before != balance:
                restored.append({"account_no": account_no, "before": before, "after": balance})

        transfers_removed = conn.execute(
            "SELECT COUNT(*) AS n FROM transfers"
        ).fetchone()["n"]
        conn.execute("DELETE FROM transfers")

        # ── 방문자가 직접 가입해 만든 계정 정리 ──
        # 시드 계정 외에는 전부 방문자가 회원가입으로 만든 것이다. 그대로 두면
        # 회원관리 목록에 계속 쌓이므로 계좌·거래내역까지 함께 지운다.
        placeholders = ",".join("?" * len(SEED_USERNAMES))
        visitors = conn.execute(
            f"SELECT id, username FROM users WHERE username NOT IN ({placeholders})",
            tuple(SEED_USERNAMES),
        ).fetchall()
        visitor_ids = [r["id"] for r in visitors]
        if visitor_ids:
            ids = ",".join("?" * len(visitor_ids))
            conn.execute(
                f"DELETE FROM transactions WHERE account_id IN "
                f"(SELECT id FROM accounts WHERE user_id IN ({ids}))",
                tuple(visitor_ids),
            )
            conn.execute(f"DELETE FROM accounts WHERE user_id IN ({ids})", tuple(visitor_ids))
            conn.execute(f"DELETE FROM users WHERE id IN ({ids})", tuple(visitor_ids))

        # ── 런타임 누적 테이블: 비우고 시드 상태로 되돌린다 ──
        # 공유 계정(demo/admin/reviewer)에 쌓인 것까지 함께 지워야 "시드 직후"가 된다
        # (지우지 않으면 이미 삭제된 계좌를 가리키는 항목이 화면에 남는다).
        # 예전에는 여기서 지우기만 했는데, 그러면 리셋 직후 백오피스 대시보드 이체 KPI·
        # 이체모니터링·이상행동 기록·문의내역·이벤트 응모자와 마이페이지 보안·즐겨찾기·
        # 예약이체·문의내역이 전부 비어서 미완성 기능처럼 보였다. 그래서 지운 뒤
        # _seed_runtime_rows() 로 시드 행을 다시 넣는다(거래내역과 같은 패턴).
        runtime_cleared = {}
        for table in ("favorites", "inquiries", "event_entries", "security_events"):
            runtime_cleared[table] = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()["n"]
            conn.execute(f"DELETE FROM {table}")

        # transfers 는 위에서 이미 비웠다. 여기서 다섯 테이블을 한꺼번에 복원한다.
        runtime_reseeded = _seed_runtime_rows(conn, now, force=True)

        # 개인신용정보 접근 로그는 '감사 로그' 성격이라 비우기보다 최근분만 남긴다
        # (전부 지우면 백오피스 패널이 비어 보이고, 안 지우면 무한정 늘어난다).
        log_total = conn.execute(
            "SELECT COUNT(*) AS n FROM admin_access_log"
        ).fetchone()["n"]
        log_trimmed = max(0, log_total - ADMIN_LOG_KEEP)
        if log_trimmed:
            conn.execute(
                "DELETE FROM admin_access_log WHERE id NOT IN "
                "(SELECT id FROM admin_access_log ORDER BY id DESC LIMIT ?)",
                (ADMIN_LOG_KEEP,),
            )

    return {
        "ok": True,
        "accounts_changed": restored,
        "transactions_reseeded": tx_removed,
        "transfers_removed": transfers_removed,
        "visitor_users_removed": [r["username"] for r in visitors],
        "runtime_rows_cleared": runtime_cleared,
        "runtime_rows_reseeded": runtime_reseeded,
        "access_log_trimmed": log_trimmed,
    }


if __name__ == "__main__":
    main()
