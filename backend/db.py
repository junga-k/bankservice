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
  chatbot_config_history(id, provider, default_model, default_style,
                          system_prompt, web_search, changed_by, created_at)  # AI은행원 설정 저장 이력

금액은 원(KRW) 정수로 저장한다(소수 없음).
공개 함수만 backend/app.py·transfer_consumer.py 에서 사용한다.
"""
from __future__ import annotations

import json
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
            CREATE TABLE IF NOT EXISTS favorites (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                bank_name    TEXT NOT NULL DEFAULT '',
                account_no   TEXT NOT NULL,
                holder_name  TEXT NOT NULL DEFAULT '',
                nickname     TEXT NOT NULL DEFAULT '',
                created_at   REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chatbot_config_history (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                provider       TEXT NOT NULL,
                default_model  TEXT NOT NULL,
                default_style  TEXT NOT NULL,
                system_prompt  TEXT NOT NULL,
                web_search     INTEGER NOT NULL DEFAULT 0,
                changed_by     TEXT NOT NULL DEFAULT '',
                created_at     REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_feedback (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                message_index   INTEGER NOT NULL,
                rating          TEXT NOT NULL,
                reasons         TEXT NOT NULL DEFAULT '',
                comment         TEXT NOT NULL DEFAULT '',
                question        TEXT NOT NULL DEFAULT '',
                answer          TEXT NOT NULL,
                created_at      REAL NOT NULL
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
            ("transfer_password_hash",
             "ALTER TABLE users ADD COLUMN transfer_password_hash TEXT NOT NULL DEFAULT ''"),
            ("is_active", "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"),
            ("agree_marketing", "ALTER TABLE users ADD COLUMN agree_marketing INTEGER NOT NULL DEFAULT 0"),
            ("agree_openbanking", "ALTER TABLE users ADD COLUMN agree_openbanking INTEGER NOT NULL DEFAULT 1"),
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


def list_transfers(offset: int = 0, limit: int = 20, status: str = "", q: str = "") -> list[dict]:
    """이체 내역 최신순 목록(Backoffice 이체모니터링용). q 있으면 출금/입금 계좌번호 부분검색."""
    with get_conn() as conn:
        clauses = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if q:
            like = f"%{q}%"
            clauses.append("(from_account LIKE ? OR to_account LIKE ?)")
            params += [like, like]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT id, from_account, to_account, to_bank, to_holder, amount, fee, memo, "
            f"status, error, scheduled_at, created_at FROM transfers "
            f"{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def count_transfers(status: str = "", q: str = "") -> int:
    with get_conn() as conn:
        clauses = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if q:
            like = f"%{q}%"
            clauses.append("(from_account LIKE ? OR to_account LIKE ?)")
            params += [like, like]
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return conn.execute(
            f"SELECT COUNT(*) FROM transfers {where}", tuple(params)
        ).fetchone()[0]


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


def stats_usage_by_category() -> list[dict]:
    """카테고리별 이벤트 수(두 소스 합산) 순위."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT category AS name, COUNT(*) AS count FROM usage_events "
            "WHERE category IS NOT NULL AND category <> '' "
            "GROUP BY category ORDER BY count DESC",
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


def update_notice(notice_id: int, title: str, content: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE notices SET title = ?, content = ? WHERE id = ?", (title, content, notice_id)
        )


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


def update_faq(faq_id: int, question: str, answer: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE faqs SET question = ?, answer = ? WHERE id = ?", (question, answer, faq_id)
        )


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


def update_document(document_id: int, title: str, category: str, description: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET title = ?, category = ?, description = ? WHERE id = ?",
            (title, category, description, document_id),
        )


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


# ── Backoffice: 문의내역(전체 조회, 읽기 전용 — 스키마에 답변 컬럼 없음) ──────
def list_all_inquiries(offset: int = 0, limit: int = 20, q: str = "") -> list[dict]:
    with get_conn() as conn:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT i.id, i.title, i.content, i.created_at, u.username, u.name "
                "FROM inquiries i JOIN users u ON u.id = i.user_id "
                "WHERE i.title LIKE ? OR i.content LIKE ? "
                "ORDER BY i.id DESC LIMIT ? OFFSET ?",
                (like, like, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT i.id, i.title, i.content, i.created_at, u.username, u.name "
                "FROM inquiries i JOIN users u ON u.id = i.user_id "
                "ORDER BY i.id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def count_all_inquiries(q: str = "") -> int:
    with get_conn() as conn:
        if q:
            like = f"%{q}%"
            return conn.execute(
                "SELECT COUNT(*) FROM inquiries WHERE title LIKE ? OR content LIKE ?",
                (like, like),
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM inquiries").fetchone()[0]


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
        if is_primary:
            conn.execute("UPDATE accounts SET is_primary = 0 WHERE user_id = ?", (user_id,))
        cur = conn.execute(
            "INSERT INTO accounts(user_id, account_no, bank_name, holder_name, balance, nickname, is_primary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, account_no, bank_name, holder_name, balance, nickname, is_primary),
        )
        return cur.lastrowid


def update_account(account_id: int, user_id: int, nickname: str | None = None,
                   is_primary: int | None = None) -> bool:
    """본인 계좌의 별칭/대표계좌 변경. 대표 지정 시 나머지는 0으로. 성공 여부 반환."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE id = ? AND user_id = ?", (account_id, user_id),
        ).fetchone()
        if row is None:
            return False
        if nickname is not None:
            conn.execute("UPDATE accounts SET nickname = ? WHERE id = ?", (nickname, account_id))
        if is_primary:
            conn.execute("UPDATE accounts SET is_primary = 0 WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE accounts SET is_primary = 1 WHERE id = ?", (account_id,))
    return True


def delete_account(account_id: int, user_id: int) -> tuple[bool, str]:
    """본인 계좌 해지. 잔액 0 && 대표계좌 아님일 때만 허용. (성공여부, 사유)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT balance, is_primary FROM accounts WHERE id = ? AND user_id = ?",
            (account_id, user_id),
        ).fetchone()
        if row is None:
            return False, "계좌를 찾을 수 없습니다."
        if row["is_primary"]:
            return False, "대표계좌는 해지할 수 없습니다. 다른 계좌를 대표로 지정한 뒤 시도하세요."
        if row["balance"] != 0:
            return False, "잔액이 남아 있어 해지할 수 없습니다. 잔액을 먼저 이체/출금하세요."
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    return True, ""


def user_account_nos(user_id: int) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT account_no FROM accounts WHERE user_id = ?", (user_id,),
        ).fetchall()
    return [r["account_no"] for r in rows]


def user_total_balance(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COALESCE(SUM(balance), 0) FROM accounts WHERE user_id = ?", (user_id,),
        ).fetchone()[0]


# ── 자주 쓰는 계좌(즐겨찾기 수취인) ─────────────────────────────────
def create_favorite(user_id: int, bank_name: str, account_no: str,
                    holder_name: str = "", nickname: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO favorites(user_id, bank_name, account_no, holder_name, nickname, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, bank_name, account_no, holder_name, nickname, time.time()),
        )
        return cur.lastrowid


def list_favorites(user_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, bank_name, account_no, holder_name, nickname, created_at "
            "FROM favorites WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_favorite(fav_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM favorites WHERE id = ? AND user_id = ?", (fav_id, user_id),
        )
    return cur.rowcount > 0


def get_user_by_username(username: str) -> dict | None:
    """로그인 검증용(password_hash·is_active 포함)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, name, role, created_at, is_active "
            "FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    return dict(row) if row else None


def get_profile(user_id: int) -> dict | None:
    """마이페이지용 프로필(로그인 비번 해시는 제외, 이체비번 존재 여부만)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, name, phone, email, role, created_at, is_active, "
            "transfer_password_hash, agree_marketing, agree_openbanking "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["has_transfer_password"] = bool((d.pop("transfer_password_hash") or "").strip())
    return d


def find_user_by_identity(name: str, phone: str) -> dict | None:
    """비밀번호 찾기용: 이름+전화가 일치하는 사용자(활성만)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, name, phone, email FROM users "
            "WHERE name = ? AND REPLACE(phone, '-', '') = ? AND is_active = 1",
            (name, re.sub(r"[^0-9]", "", phone or "")),
        ).fetchone()
    return dict(row) if row else None


def find_user_by_name_email(name: str, email: str) -> dict | None:
    """아이디 찾기용: 이름+이메일이 일치하는 사용자(활성만)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, name, phone, email FROM users "
            "WHERE name = ? AND email = ? AND is_active = 1",
            (name, (email or "").strip()),
        ).fetchone()
    return dict(row) if row else None


def get_transfer_password_hash(user_id: int) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT transfer_password_hash FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return (row["transfer_password_hash"] if row else None) or ""


def update_user(user_id: int, name: str, phone: str, email: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET name = ?, phone = ?, email = ? WHERE id = ?",
            (name, phone, email, user_id),
        )


def set_password_hash(user_id: int, password_hash: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id),
        )


def set_transfer_password_hash(user_id: int, password_hash: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET transfer_password_hash = ? WHERE id = ?", (password_hash, user_id),
        )


def set_consents(user_id: int, agree_marketing: int, agree_openbanking: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET agree_marketing = ?, agree_openbanking = ? WHERE id = ?",
            (int(agree_marketing), int(agree_openbanking), user_id),
        )


def deactivate_user(user_id: int) -> None:
    """회원 탈퇴(소프트 삭제). 로그인·인증에서 is_active=0을 차단한다."""
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))


def list_users(offset: int = 0, limit: int = 20, q: str = "", role: str = "") -> list[dict]:
    """회원 목록(password_hash·email·phone 제외 — 개인정보/보안). q 있으면 아이디/이름 부분 검색, role 있으면 권한 필터."""
    with get_conn() as conn:
        clauses = []
        params: list = []
        if q:
            like = f"%{q}%"
            clauses.append("(username LIKE ? OR name LIKE ?)")
            params += [like, like]
        if role:
            clauses.append("role = ?")
            params.append(role)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT id, username, name, role, created_at, is_active FROM users "
            f"{where} ORDER BY id LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def get_user_by_id(user_id: int) -> dict | None:
    """회원 상세용(password_hash·email·phone 제외 — 목록과 동일한 개인정보 제외 원칙)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, name, role, created_at, is_active FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


# ── 관리자 집계 (개인정보 없는 요약만) ───────────────────────────────
def count_users(q: str = "", role: str = "") -> int:
    with get_conn() as conn:
        clauses = []
        params: list = []
        if q:
            like = f"%{q}%"
            clauses.append("(username LIKE ? OR name LIKE ?)")
            params += [like, like]
        if role:
            clauses.append("role = ?")
            params.append(role)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return conn.execute(
            f"SELECT COUNT(*) FROM users {where}", tuple(params)
        ).fetchone()[0]


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


def list_user_scheduled(user_id: int) -> list[dict]:
    """본인 출금계좌의 예약/지연 대기 이체를 예정 시각 오름차순으로 반환(마이페이지용)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, from_account, to_account, to_bank, to_holder, amount, fee, "
            "status, scheduled_at, created_at FROM transfers "
            "WHERE status IN ('scheduled', 'delayed') "
            "AND from_account IN (SELECT account_no FROM accounts WHERE user_id = ?) "
            "ORDER BY scheduled_at ASC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_user_transactions(user_id: int, since_epoch: float = 0.0) -> list[dict]:
    """본인 전 계좌 통합 거래내역(최신순). 거래명세서 CSV용. since_epoch 이후만."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.account_no, a.bank_name, t.type, t.amount, t.counterparty, "
            "t.balance_after, t.created_at "
            "FROM transactions t JOIN accounts a ON a.id = t.account_id "
            "WHERE a.user_id = ? AND t.created_at >= ? "
            "ORDER BY t.created_at DESC, t.id DESC",
            (user_id, since_epoch),
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


def list_security_events(offset: int = 0, limit: int = 20, event_type: str = "",
                         username: str = "") -> list[dict]:
    """보안 이벤트 목록(최신순). event_type/username 지정 시 필터."""
    where, params = [], []
    if event_type:
        where.append("event_type = ?")
        params.append(event_type)
    if username:
        where.append("username = ?")
        params.append(username)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM security_events {clause} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
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


# ── Backoffice: AI은행원 설정 저장 이력 ──────────────────────────────
def create_chatbot_config_history(
    provider: str,
    default_model: str,
    default_style: str,
    system_prompt: str,
    web_search: bool,
    changed_by: str,
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO chatbot_config_history"
            "(provider, default_model, default_style, system_prompt, web_search, changed_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (provider, default_model, default_style, system_prompt, int(web_search), changed_by, time.time()),
        )
        return cur.lastrowid


def list_chatbot_config_history(offset: int = 0, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, provider, default_model, default_style, system_prompt, "
            "web_search, changed_by, created_at FROM chatbot_config_history "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def count_chatbot_config_history() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM chatbot_config_history").fetchone()[0]


def get_chatbot_config_history_entry(history_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, provider, default_model, default_style, system_prompt, "
            "web_search, changed_by, created_at FROM chatbot_config_history WHERE id = ?",
            (history_id,),
        ).fetchone()
    return dict(row) if row else None


# ── AI은행원 답변 평가(좋아요/싫어요) ──────────────────────────────────
def create_chat_feedback(
    conversation_id: str,
    message_index: int,
    rating: str,
    reasons: str = "",
    comment: str = "",
    question: str = "",
    answer: str = "",
) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO chat_feedback"
            "(conversation_id, message_index, rating, reasons, comment, question, answer, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, message_index, rating, reasons, comment, question, answer, time.time()),
        )
        return cur.lastrowid


def list_chat_feedback(offset: int = 0, limit: int = 20, rating: str = "") -> list[dict]:
    with get_conn() as conn:
        if rating:
            rows = conn.execute(
                "SELECT id, conversation_id, message_index, rating, reasons, comment, "
                "question, answer, created_at FROM chat_feedback WHERE rating = ? "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (rating, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, conversation_id, message_index, rating, reasons, comment, "
                "question, answer, created_at FROM chat_feedback "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
    return [dict(r) for r in rows]


def count_chat_feedback(rating: str = "") -> int:
    with get_conn() as conn:
        if rating:
            return conn.execute(
                "SELECT COUNT(*) FROM chat_feedback WHERE rating = ?", (rating,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM chat_feedback").fetchone()[0]


def chat_feedback_summary() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM chat_feedback").fetchone()[0]
        up = conn.execute("SELECT COUNT(*) FROM chat_feedback WHERE rating = 'up'").fetchone()[0]
        down = conn.execute("SELECT COUNT(*) FROM chat_feedback WHERE rating = 'down'").fetchone()[0]
    return {"total": total, "up": up, "down": down}


def chat_feedback_reason_counts() -> list[dict]:
    """싫어요 이유별 건수 순위(reasons는 JSON 배열 문자열로 저장돼 있어 파이썬에서 집계)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT reasons FROM chat_feedback WHERE rating = 'down' AND reasons != ''"
        ).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        try:
            for reason in json.loads(r["reasons"]):
                counts[reason] = counts.get(reason, 0) + 1
        except (ValueError, TypeError):
            continue
    return sorted(
        [{"name": k, "count": v} for k, v in counts.items()],
        key=lambda x: -x["count"],
    )
