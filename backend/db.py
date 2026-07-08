"""SQLite 데이터 계층 (은행 데모).

테이블:
  users(id, username, password_hash, created_at)          # 인증(Phase 3)
  accounts(id, user_id, account_no, bank_name, balance)
  transactions(id, account_id, type, amount, counterparty, created_at)
  transfers(id, from_account, to_account, amount, status, error, created_at)
  usage_events(id, source, bank, product, category, created_at)   # 이용통계
  notices(id, title, content, created_at)                  # 고객센터: 공지사항
  faqs(id, question, answer, created_at)                    # 고객센터: FAQ
  documents(id, title, category, description, created_at)   # 고객센터: 서식·약관·설명서
  inquiries(id, user_id, title, content, created_at)         # 고객센터: 문의하기

금액은 원(KRW) 정수로 저장한다(소수 없음).
공개 함수만 backend/app.py·transfer_consumer.py 에서 사용한다.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

# 프로젝트 루트의 bank.db (실행 위치와 무관)
DB_PATH = Path(__file__).resolve().parent.parent / "bank.db"

DEMO_USER_ID = 1  # 로그인 전 기본 사용자 (Phase 3에서 실제 로그인 사용자로 대체)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """테이블이 없으면 생성한다."""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL DEFAULT '',
                name          TEXT NOT NULL DEFAULT '',
                role          TEXT NOT NULL DEFAULT 'user',
                created_at    REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                account_no  TEXT UNIQUE NOT NULL,
                bank_name   TEXT NOT NULL,
                holder_name TEXT NOT NULL DEFAULT '',
                balance     INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id   INTEGER NOT NULL,
                type         TEXT NOT NULL,        -- 'in' | 'out'
                amount       INTEGER NOT NULL,
                counterparty TEXT,
                created_at   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transfers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                from_account TEXT NOT NULL,
                to_account   TEXT NOT NULL,
                to_bank      TEXT NOT NULL DEFAULT '',
                to_holder    TEXT NOT NULL DEFAULT '',
                amount       INTEGER NOT NULL,
                fee          INTEGER NOT NULL DEFAULT 0,
                memo         TEXT,
                status       TEXT NOT NULL,        -- 'pending' | 'completed' | 'failed'
                error        TEXT,
                created_at   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                source     TEXT NOT NULL,          -- 'view' | 'search'
                bank       TEXT,
                product    TEXT,
                category   TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notices (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS faqs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                question   TEXT NOT NULL,
                answer     TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                category    TEXT NOT NULL,        -- '약관' | '서식' | '설명서'
                description TEXT NOT NULL DEFAULT '',
                created_at  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inquiries (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                title      TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS security_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type   TEXT NOT NULL,          -- password_fail | limit_once | limit_daily | new_payee
                username     TEXT NOT NULL DEFAULT '',
                from_account TEXT NOT NULL DEFAULT '',
                to_account   TEXT NOT NULL DEFAULT '',
                amount       INTEGER NOT NULL DEFAULT 0,
                detail       TEXT NOT NULL DEFAULT '',
                created_at   REAL NOT NULL
            );
            """
        )
        # 기존 DB 마이그레이션: users에 name/role, transfers에 sender_memo,
        # transactions에 balance_after 컬럼 없으면 추가
        for col, ddl in (
            ("name", "ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT ''"),
            ("role", "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"),
            ("phone", "ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT ''"),
            ("email", "ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''"),
            ("sender_memo", "ALTER TABLE transfers ADD COLUMN sender_memo TEXT"),
            ("balance_after", "ALTER TABLE transactions ADD COLUMN balance_after INTEGER"),
            ("nickname", "ALTER TABLE accounts ADD COLUMN nickname TEXT NOT NULL DEFAULT ''"),
            ("is_primary", "ALTER TABLE accounts ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0"),
            ("scheduled_at", "ALTER TABLE transfers ADD COLUMN scheduled_at REAL"),
        ):
            try:
                conn.execute(ddl)
            except Exception:
                pass  # 이미 존재

        _backfill_balance_after(conn)


def _backfill_balance_after(conn: sqlite3.Connection) -> None:
    """balance_after가 비어있는 기존 거래내역을, 계좌 현재 잔액에서 최신순으로 거슬러 올라가며 채운다.
    이미 다 채워져 있으면(balance_after가 NULL인 행이 없으면) 아무 일도 하지 않는다."""
    accounts = conn.execute("SELECT id, balance FROM accounts").fetchall()
    for acc in accounts:
        rows = conn.execute(
            "SELECT id, type, amount FROM transactions "
            "WHERE account_id = ? AND balance_after IS NULL "
            "ORDER BY created_at DESC, id DESC",
            (acc["id"],),
        ).fetchall()
        if not rows:
            continue
        running = acc["balance"]
        for row in rows:
            conn.execute(
                "UPDATE transactions SET balance_after = ? WHERE id = ?", (running, row["id"]),
            )
            signed = row["amount"] if row["type"] == "in" else -row["amount"]
            running -= signed


# ── 계좌 조회 ────────────────────────────────────────────────────────
def list_accounts(user_id: int = DEMO_USER_ID) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, account_no, bank_name, holder_name, balance, nickname, is_primary "
            "FROM accounts WHERE user_id = ? ORDER BY is_primary DESC, id",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_account(account_id: int, user_id: int = DEMO_USER_ID) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, account_no, bank_name, holder_name, balance FROM accounts "
            "WHERE id = ? AND user_id = ?",
            (account_id, user_id),
        ).fetchone()
    return dict(row) if row else None


def lookup_account(account_no: str) -> dict | None:
    """받는 계좌의 예금주·은행 조회. 대시(-) 유무와 무관하게 숫자만 비교한다."""
    digits = re.sub(r"[^0-9]", "", account_no)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT account_no, bank_name, holder_name FROM accounts "
            "WHERE REPLACE(account_no, '-', '') = ?",
            (digits,),
        ).fetchone()
    return dict(row) if row else None


def list_transactions(account_id: int, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT type, amount, counterparty, balance_after, created_at FROM transactions "
            "WHERE account_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 이체 ─────────────────────────────────────────────────────────────
def create_transfer(from_account: str, to_account: str, amount: int,
                    to_bank: str = "", to_holder: str = "",
                    fee: int = 0, memo: str | None = None,
                    sender_memo: str | None = None,
                    status: str = "pending", scheduled_at: float | None = None) -> int:
    """이체 레코드 생성 후 id 반환. 예약/지연이면 status='scheduled'/'delayed' + scheduled_at."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO transfers"
            "(from_account, to_account, to_bank, to_holder, amount, fee, memo, sender_memo, "
            "status, scheduled_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (from_account, to_account, to_bank, to_holder, amount, fee, memo, sender_memo,
             status, scheduled_at, time.time()),
        )
        return cur.lastrowid


def pop_due_scheduled(now: float) -> list[int]:
    """실행 시각이 도래한 예약/지연 이체를 pending으로 원자적 전환하고 그 id 목록을 반환.
    폴러가 호출 → 반환된 id들을 process_transfer로 처리한다."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM transfers "
            "WHERE status IN ('scheduled', 'delayed') AND scheduled_at IS NOT NULL AND scheduled_at <= ?",
            (now,),
        ).fetchall()
        ids = [r["id"] for r in rows]
        for tid in ids:
            conn.execute("UPDATE transfers SET status='pending' WHERE id=?", (tid,))
    return ids


def cancel_scheduled(transfer_id: int, user_id: int) -> bool:
    """본인 소유 출금계좌의 예약/지연 이체를 취소(canceled). 성공 여부 반환."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM transfers WHERE id=? "
            "AND from_account IN (SELECT account_no FROM accounts WHERE user_id=?)",
            (transfer_id, user_id),
        ).fetchone()
        if row is None or row["status"] not in ("scheduled", "delayed"):
            return False
        conn.execute("UPDATE transfers SET status='canceled' WHERE id=?", (transfer_id,))
    return True


def fail_transfer(transfer_id: int, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE transfers SET status='failed', error=? WHERE id=?",
            (error, transfer_id),
        )


def get_transfer(transfer_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, from_account, to_account, to_bank, to_holder, amount, fee, memo, "
            "sender_memo, status, error, created_at FROM transfers WHERE id = ?",
            (transfer_id,),
        ).fetchone()
    return dict(row) if row else None


def list_transfers(offset: int = 0, limit: int = 20, status: str = "") -> list[dict]:
    """이체 내역 최신순 목록(Backoffice 이체모니터링용)."""
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT id, from_account, to_account, to_bank, to_holder, amount, fee, memo, "
                "status, error, scheduled_at, created_at FROM transfers WHERE status = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, from_account, to_account, to_bank, to_holder, amount, fee, memo, "
                "status, error, scheduled_at, created_at FROM transfers ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def count_transfers(status: str = "") -> int:
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM transfers WHERE status = ?", (status,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]


def account_exists(account_no: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM accounts WHERE account_no = ?", (account_no,)
        ).fetchone()
    return row is not None


def process_transfer(transfer_id: int) -> str:
    """이체를 원자적으로 처리한다(컨슈머가 호출).

    출금계좌 잔액 차감 + (내부 계좌면) 입금계좌 증액 + 거래내역 기록 +
    transfers.status 갱신을 하나의 트랜잭션으로 수행한다.
    반환: 최종 상태 'completed' | 'failed'
    """
    conn = get_conn()
    try:
        conn.isolation_level = None  # 수동 트랜잭션
        conn.execute("BEGIN IMMEDIATE")

        tr = conn.execute(
            "SELECT from_account, to_account, amount, fee, status, to_holder, memo, sender_memo "
            "FROM transfers WHERE id = ?",
            (transfer_id,),
        ).fetchone()
        if tr is None:
            conn.execute("ROLLBACK")
            return "failed"
        if tr["status"] != "pending":
            conn.execute("ROLLBACK")
            return tr["status"]  # 이미 처리됨(중복 방지)

        from_no, to_no = tr["from_account"], tr["to_account"]
        amount, fee = tr["amount"], tr["fee"]
        total = amount + fee
        # 통장 표시: 비워두면 상대방 이름으로 기본값(계좌번호는 최종 폴백)
        out_counterparty = tr["sender_memo"] or tr["to_holder"] or to_no
        in_counterparty = tr["memo"] or from_no  # src 조회 후 holder_name으로 보강

        src = conn.execute(
            "SELECT id, balance, holder_name FROM accounts WHERE account_no = ?", (from_no,)
        ).fetchone()
        if src is not None:
            in_counterparty = tr["memo"] or src["holder_name"] or from_no

        # 검증
        err = None
        if src is None:
            err = "출금 계좌를 찾을 수 없습니다."
        elif amount <= 0:
            err = "이체 금액이 올바르지 않습니다."
        elif src["balance"] < total:
            err = "잔액이 부족합니다."

        if err:
            conn.execute(
                "UPDATE transfers SET status='failed', error=? WHERE id=?",
                (err, transfer_id),
            )
            conn.execute("COMMIT")
            return "failed"

        now = time.time()
        # 출금 (금액 + 수수료)
        conn.execute(
            "UPDATE accounts SET balance = balance - ? WHERE id = ?",
            (total, src["id"]),
        )
        balance_after_amount = src["balance"] - amount
        conn.execute(
            "INSERT INTO transactions(account_id, type, amount, counterparty, balance_after, created_at) "
            "VALUES (?, 'out', ?, ?, ?, ?)",
            (src["id"], amount, out_counterparty, balance_after_amount, now),
        )
        # 수수료가 있으면 별도 출금 내역
        if fee > 0:
            conn.execute(
                "INSERT INTO transactions(account_id, type, amount, counterparty, balance_after, created_at) "
                "VALUES (?, 'out', ?, '이체수수료', ?, ?)",
                (src["id"], fee, balance_after_amount - fee, now),
            )
        # 입금(내부 계좌인 경우)
        dst = conn.execute(
            "SELECT id, balance FROM accounts WHERE account_no = ?", (to_no,)
        ).fetchone()
        if dst is not None:
            conn.execute(
                "UPDATE accounts SET balance = balance + ? WHERE id = ?",
                (amount, dst["id"]),
            )
            conn.execute(
                "INSERT INTO transactions(account_id, type, amount, counterparty, balance_after, created_at) "
                "VALUES (?, 'in', ?, ?, ?, ?)",
                (dst["id"], amount, in_counterparty, dst["balance"] + amount, now),
            )

        conn.execute(
            "UPDATE transfers SET status='completed', error=NULL WHERE id=?",
            (transfer_id,),
        )
        conn.execute("COMMIT")
        return "completed"
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        conn.close()


# ── 이용 통계 (Phase 2) ──────────────────────────────────────────────
def add_usage_event(source: str, bank: str | None = None,
                    product: str | None = None, category: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO usage_events(source, bank, product, category, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source, bank, product, category, time.time()),
        )


def stats_banks(limit: int = 10) -> list[dict]:
    """은행별 이벤트 수(두 소스 합산) 순위."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT bank AS name, COUNT(*) AS count FROM usage_events "
            "WHERE bank IS NOT NULL AND bank <> '' "
            "GROUP BY bank ORDER BY count DESC, bank LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def stats_top_products(per_category: int = 5) -> dict[str, list[dict]]:
    """카테고리별 상위 상품(두 소스 합산)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT category, product, COUNT(*) AS count FROM usage_events "
            "WHERE product IS NOT NULL AND product <> '' "
            "GROUP BY category, product",
        ).fetchall()
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        cat = r["category"] or "기타"
        by_cat.setdefault(cat, []).append({"product": r["product"], "count": r["count"]})
    for cat, items in by_cat.items():
        items.sort(key=lambda x: (-x["count"], x["product"]))
        by_cat[cat] = items[:per_category]
    return by_cat


def stats_usage_summary() -> dict:
    """전체/조회(view)/검색(search) 이벤트 수(Backoffice 이용통계 요약)."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
        view = conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE source = 'view'"
        ).fetchone()[0]
        search = conn.execute(
            "SELECT COUNT(*) FROM usage_events WHERE source = 'search'"
        ).fetchone()[0]
    return {"total": total, "view": view, "search": search}


def stats_usage_daily(days: int = 14) -> list[dict]:
    """최근 N일 일별 이벤트 수(Backoffice 이용통계 추이)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT date(created_at, 'unixepoch') AS day, COUNT(*) AS count "
            "FROM usage_events WHERE created_at >= ? GROUP BY day ORDER BY day",
            (time.time() - days * 86400,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 고객센터: 공지사항 ──────────────────────────────────────────────
def create_notice(title: str, content: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notices(title, content, created_at) VALUES (?, ?, ?)",
            (title, content, time.time()),
        )
        return cur.lastrowid


def delete_notice(notice_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM notices WHERE id = ?", (notice_id,))


def list_notices(offset: int = 0, limit: int = 20, q: str = "") -> list[dict]:
    with get_conn() as conn:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT id, title, content, created_at FROM notices "
                "WHERE title LIKE ? OR content LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (like, like, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, content, created_at FROM notices "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def count_notices(q: str = "") -> int:
    with get_conn() as conn:
        if q:
            like = f"%{q}%"
            return conn.execute(
                "SELECT COUNT(*) FROM notices WHERE title LIKE ? OR content LIKE ?", (like, like),
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]


# ── 고객센터: FAQ ────────────────────────────────────────────────────
def create_faq(question: str, answer: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO faqs(question, answer, created_at) VALUES (?, ?, ?)",
            (question, answer, time.time()),
        )
        return cur.lastrowid


def delete_faq(faq_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM faqs WHERE id = ?", (faq_id,))


def list_faqs(offset: int = 0, limit: int = 20, q: str = "") -> list[dict]:
    with get_conn() as conn:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT id, question, answer, created_at FROM faqs "
                "WHERE question LIKE ? OR answer LIKE ? ORDER BY id LIMIT ? OFFSET ?",
                (like, like, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, question, answer, created_at FROM faqs ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def count_faqs(q: str = "") -> int:
    with get_conn() as conn:
        if q:
            like = f"%{q}%"
            return conn.execute(
                "SELECT COUNT(*) FROM faqs WHERE question LIKE ? OR answer LIKE ?", (like, like),
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM faqs").fetchone()[0]


# ── 고객센터: 서식·약관·설명서 ────────────────────────────────────────
def create_document(title: str, category: str, description: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO documents(title, category, description, created_at) VALUES (?, ?, ?, ?)",
            (title, category, description, time.time()),
        )
        return cur.lastrowid


def delete_document(document_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))


def list_documents(offset: int = 0, limit: int = 20, q: str = "") -> list[dict]:
    with get_conn() as conn:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT id, title, category, description, created_at FROM documents "
                "WHERE title LIKE ? OR description LIKE ? ORDER BY id LIMIT ? OFFSET ?",
                (like, like, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, category, description, created_at FROM documents "
                "ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def count_documents(q: str = "") -> int:
    with get_conn() as conn:
        if q:
            like = f"%{q}%"
            return conn.execute(
                "SELECT COUNT(*) FROM documents WHERE title LIKE ? OR description LIKE ?",
                (like, like),
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


# ── 고객센터: 문의하기 ──────────────────────────────────────────────
def create_inquiry(user_id: int, title: str, content: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO inquiries(user_id, title, content, created_at) VALUES (?, ?, ?, ?)",
            (user_id, title, content, time.time()),
        )
        return cur.lastrowid


def list_inquiries(user_id: int, offset: int = 0, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, content, created_at FROM inquiries "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def count_inquiries(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM inquiries WHERE user_id = ?", (user_id,)
        ).fetchone()[0]


# ── 사용자/인증 (Phase 3) ────────────────────────────────────────────
def create_user(username: str, password_hash: str, name: str = "",
                role: str = "user", phone: str = "", email: str = "") -> int:
    """사용자 생성 후 id 반환. username 중복 시 sqlite3.IntegrityError."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, name, role, phone, email, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (username, password_hash, name, role, phone, email, time.time()),
        )
        return cur.lastrowid


def create_account(user_id: int, account_no: str, bank_name: str,
                    holder_name: str, balance: int = 0,
                    nickname: str = "", is_primary: int = 0) -> int:
    """계좌 개설 후 id 반환. account_no 중복 시 sqlite3.IntegrityError."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO accounts(user_id, account_no, bank_name, holder_name, balance, nickname, is_primary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, account_no, bank_name, holder_name, balance, nickname, is_primary),
        )
        return cur.lastrowid


def get_user_by_username(username: str) -> dict | None:
    """로그인 검증용(password_hash 포함)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, name, role, created_at "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    return dict(row) if row else None


def list_users(offset: int = 0, limit: int = 20, q: str = "") -> list[dict]:
    """회원 목록(password_hash 제외 — 개인정보/보안). q 있으면 아이디/이름 부분 검색."""
    with get_conn() as conn:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT id, username, name, role, created_at FROM users "
                "WHERE username LIKE ? OR name LIKE ? ORDER BY id LIMIT ? OFFSET ?",
                (like, like, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, username, name, role, created_at FROM users "
                "ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


# ── 관리자 집계 (개인정보 없는 요약만) ───────────────────────────────
def count_users(q: str = "") -> int:
    with get_conn() as conn:
        if q:
            like = f"%{q}%"
            return conn.execute(
                "SELECT COUNT(*) FROM users WHERE username LIKE ? OR name LIKE ?",
                (like, like),
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def count_accounts() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]


def sum_balance() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COALESCE(SUM(balance), 0) FROM accounts").fetchone()[0]


def transfer_summary() -> dict:
    """이체 집계: 총 건수·상태별 건수·완료 금액 합계(개별 로그 없음)."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM transfers").fetchone()[0]
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM transfers GROUP BY status"
        ).fetchall()
        completed_amt = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM transfers WHERE status='completed'"
        ).fetchone()[0]
    by_status = {r["status"]: r["n"] for r in rows}
    return {
        "total": total,
        "completed": by_status.get("completed", 0),
        "failed": by_status.get("failed", 0),
        "pending": by_status.get("pending", 0),
        "scheduled": by_status.get("scheduled", 0),
        "delayed": by_status.get("delayed", 0),
        "canceled": by_status.get("canceled", 0),
        "completed_amount": completed_amt,
    }


def list_scheduled_pending() -> list[dict]:
    """실행 대기 중인 예약/지연 이체를 예정 시각 오름차순으로 반환."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, from_account, to_account, to_bank, to_holder, amount, fee, "
            "status, scheduled_at, created_at FROM transfers "
            "WHERE status IN ('scheduled', 'delayed') "
            "ORDER BY scheduled_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def log_security_event(event_type: str, username: str = "", from_account: str = "",
                       to_account: str = "", amount: int = 0, detail: str = "") -> None:
    """이체 보안 이벤트(거부·경고)를 기록. 실패해도 호출부 흐름을 막지 않는다."""
    import time as _time
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO security_events "
                "(event_type, username, from_account, to_account, amount, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_type, username, from_account, to_account, int(amount or 0),
                 detail, _time.time()),
            )
    except Exception:
        pass


def list_security_events(offset: int = 0, limit: int = 20, event_type: str = "") -> list[dict]:
    """보안 이벤트 목록(최신순). event_type 지정 시 해당 유형만."""
    with get_conn() as conn:
        if event_type:
            rows = conn.execute(
                "SELECT * FROM security_events WHERE event_type = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (event_type, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM security_events ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def security_event_summary() -> dict:
    """유형별 총 건수 + 최근 24시간 건수."""
    import time as _time
    since = _time.time() - 86400
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM security_events").fetchone()[0]
        by_type = {
            r["event_type"]: r["n"]
            for r in conn.execute(
                "SELECT event_type, COUNT(*) AS n FROM security_events GROUP BY event_type"
            ).fetchall()
        }
        last24h = conn.execute(
            "SELECT COUNT(*) FROM security_events WHERE created_at >= ?", (since,)
        ).fetchone()[0]
    return {"total": total, "by_type": by_type, "last_24h": last24h}


def is_new_payee(user_id: int, to_account: str) -> bool:
    """사용자가 이 수취계좌로 '완료된 이체' 이력이 없으면 True(처음 보내는 계좌).
    계좌번호는 대시(-) 유무와 무관하게 숫자만 비교한다."""
    import re
    digits = re.sub(r"[^0-9]", "", to_account or "")
    if not digits:
        return True
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM transfers "
            "WHERE status='completed' "
            "AND REPLACE(to_account, '-', '') = ? "
            "AND from_account IN (SELECT account_no FROM accounts WHERE user_id = ?)",
            (digits, user_id),
        ).fetchone()[0]
    return n == 0


def sum_user_transfers_today(account_nos: list[str], since_epoch: float) -> int:
    """오늘(since_epoch 이후) 해당 출금계좌들의 completed+pending 이체 금액 합계."""
    if not account_nos:
        return 0
    placeholders = ",".join("?" * len(account_nos))
    with get_conn() as conn:
        return conn.execute(
            f"SELECT COALESCE(SUM(amount), 0) FROM transfers "
            f"WHERE from_account IN ({placeholders}) "
            f"AND status IN ('completed', 'pending') AND created_at >= ?",
            (*account_nos, since_epoch),
        ).fetchone()[0]
