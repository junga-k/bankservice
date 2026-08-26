"""은행 데모 데이터 시드.

내 계좌(데모 사용자 '홍길동') 3개 + 과거 거래내역,
그리고 이체 받는 사람으로 쓸 다른 사용자 계좌 몇 개를 넣는다.
이미 시드되어 있으면 건너뛴다(멱등). 실행: .venv/bin/python seed_bank.py

⚠️ 전부 가짜 데이터다(실제 계좌·돈·개인정보 아님).
"""
from __future__ import annotations

import time

from backend import auth, db

DEMO_USER = "demo"
DEMO_PASSWORD = "demo1234"  # 관리자가 방문자 기능(계좌조회·이체·은행원 등) 테스트에 쓰는 고정 비밀번호
DEMO_TRANSFER_PIN = "246810"  # 데모 이체 비밀번호(숫자 6자리)
DEMO_HOLDER = "홍길동"

# 관리자 계정(고정): 데모 계정 정보를 확인할 수 있는 유일한 계정
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin1234"
ADMIN_TRANSFER_PIN = "135790"  # 관리자 이체 비밀번호(숫자 6자리)
ADMIN_HOLDER = "관리자"


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
            print(f"이미 시드됨 (user '{DEMO_USER}' 존재) — 이체PIN/관리자 보정 후 건너뜀")
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

        # ── 관리자 계정 ──
        _ensure_admin(conn, now)

    print(f"시드 완료: 내 계좌 {len(MY_ACCOUNTS)}개(홍길동), "
          f"받는 계좌 {len(OTHER_ACCOUNTS)}개, 관리자 계정 '{ADMIN_USER}'")


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


# ── 공개 데모 잔액 리셋 ──────────────────────────────────────────────
# 배포된 데모는 계정을 여럿이 공유하므로, 방문자가 이체할 때마다 잔액이 줄어들고
# 언젠가 잔액 부족으로 이체가 실패한다. 아래 함수가 시드 직후 상태로 되돌린다.
# 위의 시드 상수(MY_ACCOUNTS / ADMIN_ACCOUNTS / OTHER_ACCOUNTS / *_PAST_TX)를 그대로
# 재사용하므로 시드 값을 바꾸면 리셋 기준도 자동으로 따라온다(값이 두 군데로 갈리지 않음).

def _seeded_accounts():
    """(계좌번호, 시드 잔액, 시드 거래내역) 목록."""
    for account_no, _bank, balance in MY_ACCOUNTS:
        yield account_no, balance, PAST_TX.get(account_no, [])
    for account_no, _bank, balance in ADMIN_ACCOUNTS:
        yield account_no, balance, ADMIN_PAST_TX.get(account_no, [])
    for _username, _holder, account_no, _bank, balance in OTHER_ACCOUNTS:
        yield account_no, balance, []


def reset_demo_data() -> dict:
    """시드된 계좌의 잔액·거래내역을 원래대로 되돌리고 이체 기록을 비운다.

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

    return {
        "ok": True,
        "accounts_changed": restored,
        "transactions_reseeded": tx_removed,
        "transfers_removed": transfers_removed,
    }


if __name__ == "__main__":
    main()
