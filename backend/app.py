"""FastAPI 백엔드: 정적 사이트 서빙 + 은행 데모 REST API + Kafka 이체 프로듀서.

실행: .venv/bin/uvicorn backend.app:app --port 8000
- 정적 사이트(site/)를 함께 서빙하므로 python -m http.server 는 불필요.
- 이체는 Kafka(transfer-requests 토픽)로 발행 → transfer_consumer.py 가 처리.
"""
from __future__ import annotations

import csv
import hmac
import io
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

import requests
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import fss_fetcher
import llm
from backend import auth, db, infra_metrics, kafka_io

SITE_DIR = Path(__file__).resolve().parent.parent / "site"

app = FastAPI(title="매치뱅크 백엔드")

# 챗(Streamlit :8501)에서 /api/track/search 호출 → CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000", "http://localhost:8501",
        "http://127.0.0.1:8000", "http://127.0.0.1:8501",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 읽기전용 데모 모드 ────────────────────────────────────────────────
# 공개 배포본은 README에 admin 계정을 공개하므로, 누구나 백오피스에 로그인해 공지·FAQ·배너
# 같은 데모 데이터를 지울 수 있다. 그러면 다음 방문자가 빈 화면을 보게 되므로, 프로덕션에서만
# DEMO_READONLY=1 로 /api/admin/* 의 쓰기 요청을 막는다(조회는 전부 허용 — 백오피스를 그대로
# 둘러볼 수 있어야 데모의 의미가 있다). 로컬은 환경변수가 없으므로 아무 제한이 없다.
DEMO_READONLY = os.environ.get("DEMO_READONLY", "").strip().lower() in ("1", "true", "yes")

# 저장하지 않는 실험 기능이라 읽기전용 모드에서도 허용한다(프롬프트 A/B 비교).
_READONLY_EXEMPT_PATHS = {"/api/admin/prompt-ab-test"}


@app.middleware("http")
async def _demo_readonly_guard(request, call_next):
    if (DEMO_READONLY
            and request.method not in ("GET", "HEAD", "OPTIONS")
            and request.url.path.startswith("/api/admin/")
            and request.url.path not in _READONLY_EXEMPT_PATHS):
        return JSONResponse(
            status_code=403,
            content={"detail": "공개 데모 환경에서는 백오피스 데이터를 변경할 수 없습니다. "
                               "직접 실행해보시려면 README의 로컬 실행 안내를 참고하세요."},
        )
    return await call_next(request)


@app.middleware("http")
async def _db_request_scope(request, call_next):
    # 요청 하나 동안 db 연결을 하나만 열어 재사용한다(Turso는 연결 하나 여는 데만
    # ~550~600ms — 요청당 db.py 호출이 여러 번이면 그만큼 배로 늘어나던 것을 방지).
    with db.request_scope():
        return await call_next(request)


@app.on_event("startup")
def _startup() -> None:
    # init_db()는 로컬(sqlite3)에서만 자동 실행한다. 이유는 비용 차이다 —
    # 로컬은 같은 디스크의 파일이라 쿼리 1회가 사실상 0ms 라 41회(스키마 18 + ALTER 14 +
    # balance_after 백필)를 돌려도 0.02초지만, 배포 환경은 Turso 원격이라 쿼리 1회당
    # 300~650ms(vercel-deploy-progress.md 실측)여서 같은 41회가 약 16초가 된다.
    # 서버리스는 콜드스타트마다 이 startup 이 다시 도는데, 테이블은 이미 다 있으므로
    # 그 16초는 "혹시 없나" 확인에만 쓰이고 방문자는 그동안 백지 화면을 본다.
    #
    # 그래서 배포 환경에서는 건너뛰고, 스키마를 바꾼 뒤에는 POST /api/maintenance/init-db
    # 를 한 번 호출해 반영한다(아래 참고). 분기 조건은 db.py 가 엔진 선택에 쓰는 것과
    # 같은 TURSO_DATABASE_URL 이라 판단 기준이 한 곳에 모인다.
    if not os.environ.get("TURSO_DATABASE_URL"):
        db.init_db()
    _start_scheduled_poller()


# ── 예약/지연 이체 폴러 (백그라운드 스레드) ──────────────────────────
_poller_started = False
_poller_last_run = 0.0   # 마지막 폴링 사이클 완료 시각(하트비트)


def _start_scheduled_poller() -> None:
    """실행 시각이 도래한 예약/지연 이체를 주기적으로 처리하는 데몬 스레드."""
    global _poller_started
    if _poller_started:
        return
    _poller_started = True

    import threading

    def _loop():
        global _poller_last_run
        while True:
            try:
                # 사이클 하나 동안 db 연결을 하나로 공유하고 사이클이 끝나면 확실히 닫는다
                # (HTTP 요청의 request_scope와 같은 메커니즘 — 이 스레드는 별도 컨텍스트라
                # HTTP 요청의 연결과 섞이지 않는다).
                with db.request_scope():
                    for tid in db.pop_due_scheduled(time.time()):
                        try:
                            db.process_transfer(tid)
                        except Exception:
                            pass
            except Exception:
                pass
            _poller_last_run = time.time()   # 하트비트 갱신
            time.sleep(15)

    threading.Thread(target=_loop, daemon=True).start()


def get_poller_status() -> dict:
    """예약 이체 폴러의 기동 여부와 마지막 실행 시각."""
    return {"started": _poller_started, "last_run": _poller_last_run}


# ── Kafka 프로듀서 (지연 초기화) ─────────────────────────────────────
_producer = None


def _get_producer():
    global _producer
    if _producer is None:
        _producer = kafka_io.make_producer()
    return _producer


# ── 요청 모델 ────────────────────────────────────────────────────────
# 타행 이체 수수료(원). 같은 은행이면 면제.
TRANSFER_FEE = 500
# 이체 한도(원) — 비밀번호(간편) 인증 수준에 맞춘 한도
TRANSFER_LIMIT = 5_000_000        # 1회 500만원
DAILY_TRANSFER_LIMIT = 10_000_000  # 1일 누적 1,000만원


class TransferReq(BaseModel):
    from_account: str
    to_account: str
    amount: int
    memo: str | None = None
    sender_memo: str | None = None
    password: str = ""
    scheduled_at: float | None = None   # 예약 실행 시각(epoch). 미래면 예약이체.
    delay_minutes: int = 0              # 지연이체(분). >0이면 now+분 후 실행, 취소 가능.


class ViewReq(BaseModel):
    bank: str | None = None
    product: str | None = None
    category: str | None = None


class SearchItem(BaseModel):
    bank: str | None = None
    product: str | None = None
    category: str | None = None


class SearchReq(BaseModel):
    items: list[SearchItem]


class SignupReq(BaseModel):
    username: str
    password: str
    transfer_password: str = ""    # 이체 비밀번호(숫자 6자리)
    name: str
    phone: str = ""
    email: str = ""
    bank_name: str = ""
    account_no: str = ""
    account_holder: str = ""
    nickname: str = ""
    is_primary: bool = True
    agree_openbanking: bool = False
    agree_marketing: bool = False


class InquiryReq(BaseModel):
    title: str
    content: str


class LoginReq(BaseModel):
    username: str
    password: str


# ── 인증 ─────────────────────────────────────────────────────────────
# 관리자가 방문자 입장에서 계좌조회·거래내역·이체·AI은행원 등 사이트 기능을
# 테스트할 때 쓰는 데모 계정(seed_bank.py에서 생성). 고정값이라 여기서만 노출.
DEMO_ACCOUNT_INFO = {
    "username": "demo",
    "password": "demo1234",
    "note": "계좌조회·거래내역·이체·AI은행원 등 방문자 기능 테스트용 계정입니다.",
}


@app.post("/api/signup")
def signup(req: SignupReq):
    username = req.username.strip()
    if not username or len(req.password) < 4:
        raise HTTPException(status_code=400, detail="아이디를 입력하고 비밀번호는 4자 이상 입력하세요.")
    if db.get_user_by_username(username) is not None:
        raise HTTPException(status_code=400, detail="이미 사용 중인 아이디입니다.")

    name = req.name.strip() or username

    # 연락처 형식 검증
    phone_digits = re.sub(r"[^0-9]", "", req.phone)
    if not (10 <= len(phone_digits) <= 11):
        raise HTTPException(status_code=400, detail="전화번호를 정확히 입력하세요.")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", req.email.strip()):
        raise HTTPException(status_code=400, detail="이메일 형식이 올바르지 않습니다.")

    # 계좌 등록(필수) + 실명확인
    bank_name = req.bank_name.strip()
    account_no = req.account_no.strip()
    if not bank_name or not account_no:
        raise HTTPException(status_code=400, detail="등록할 계좌 정보를 입력하세요.")
    if req.account_holder.strip() != name:
        raise HTTPException(status_code=400, detail="예금주명이 가입자 이름과 일치하지 않습니다.")
    if not req.agree_openbanking:
        raise HTTPException(status_code=400, detail="오픈뱅킹 이용에 동의해야 합니다.")
    if not re.match(r"^\d{6}$", req.transfer_password or ""):
        raise HTTPException(status_code=400, detail="이체 비밀번호는 숫자 6자리로 설정하세요.")
    # 계좌 중복은 회원 생성 전에 먼저 확인(중간 실패로 계정만 생성되는 것을 방지).
    # lookup_account는 숫자만 비교하므로 대시 표기 차이까지 잡아낸다.
    if db.lookup_account(account_no) is not None:
        raise HTTPException(status_code=400, detail="이미 등록된 계좌번호입니다.")

    password_hash = auth.hash_password(req.password)
    user_id = db.create_user(
        username, password_hash, name=name, phone=phone_digits, email=req.email.strip(),
    )
    db.set_transfer_password_hash(user_id, auth.hash_password(req.transfer_password))
    db.set_consents(user_id, 1 if req.agree_marketing else 0, 1 if req.agree_openbanking else 0)
    try:
        db.create_account(
            user_id, account_no, bank_name, name, balance=0,
            nickname=req.nickname.strip(), is_primary=1 if req.is_primary else 0,
        )
    except (sqlite3.IntegrityError, ValueError):
        # libsql은 UNIQUE 제약 위반 시 sqlite3.IntegrityError 대신 ValueError를 던진다
        raise HTTPException(status_code=400, detail="이미 등록된 계좌번호입니다.")

    user = db.get_user_by_username(username)
    token = auth.make_token(user)
    return {"token": token, "username": user["username"], "name": user["name"], "role": user["role"]}


@app.post("/api/login")
def login(req: LoginReq):
    user = db.get_user_by_username(req.username.strip())
    if user is None or not user["password_hash"] or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    if not user.get("is_active", 1):
        raise HTTPException(status_code=401, detail="탈퇴한 계정입니다. 로그인할 수 없습니다.")
    token = auth.make_token(user)
    return {"token": token, "username": user["username"], "name": user["name"], "role": user["role"]}


@app.get("/api/me")
def me(user: dict = Depends(auth.get_current_user)):
    return user


# ── 마이페이지: 프로필·보안 ──────────────────────────────────────────
class ProfileReq(BaseModel):
    name: str
    phone: str = ""
    email: str = ""


class PasswordReq(BaseModel):
    current_password: str
    new_password: str


class TransferPwReq(BaseModel):
    login_password: str          # 본인 확인은 로그인 비밀번호로
    new_transfer_password: str   # 숫자 6자리


class ConsentReq(BaseModel):
    agree_marketing: bool = False
    agree_openbanking: bool = True


class WithdrawReq(BaseModel):
    password: str


@app.get("/api/me/profile")
def get_my_profile(user: dict = Depends(auth.get_current_user)):
    prof = db.get_profile(user["id"])
    if prof is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    return prof


@app.put("/api/me/profile")
def update_my_profile(req: ProfileReq, user: dict = Depends(auth.get_current_user)):
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="이름을 입력하세요.")
    phone_digits = re.sub(r"[^0-9]", "", req.phone)
    if not (10 <= len(phone_digits) <= 11):
        raise HTTPException(status_code=400, detail="전화번호를 정확히 입력하세요.")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", req.email.strip()):
        raise HTTPException(status_code=400, detail="이메일 형식이 올바르지 않습니다.")
    db.update_user(user["id"], name, phone_digits, req.email.strip())
    return {"ok": True}


@app.put("/api/me/password")
def change_my_password(req: PasswordReq, user: dict = Depends(auth.get_current_user)):
    acct = db.get_user_by_username(user["username"])
    if acct is None or not auth.verify_password(req.current_password, acct["password_hash"]):
        # 401은 프런트(apiFetch)에서 "세션 무효 → 강제 로그아웃"으로 전역 처리된다.
        # 이건 로그인 세션은 멀쩡하고 재확인용 비밀번호만 틀린 것이므로 403이 맞다
        # (2026-08-10, 이체 비밀번호 오입력 시 로그아웃되던 버그와 같은 패턴 일괄 수정).
        raise HTTPException(status_code=403, detail="현재 비밀번호가 올바르지 않습니다.")
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="새 비밀번호는 4자 이상 입력하세요.")
    db.set_password_hash(user["id"], auth.hash_password(req.new_password))
    return {"ok": True}


@app.put("/api/me/transfer-password")
def change_my_transfer_password(req: TransferPwReq, user: dict = Depends(auth.get_current_user)):
    """이체 비밀번호 설정·변경·분실재설정 공용. 본인 확인은 로그인 비밀번호로."""
    acct = db.get_user_by_username(user["username"])
    if acct is None or not auth.verify_password(req.login_password, acct["password_hash"]):
        raise HTTPException(status_code=403, detail="로그인 비밀번호가 올바르지 않습니다.")
    if not re.match(r"^\d{6}$", req.new_transfer_password or ""):
        raise HTTPException(status_code=400, detail="이체 비밀번호는 숫자 6자리로 설정하세요.")
    db.set_transfer_password_hash(user["id"], auth.hash_password(req.new_transfer_password))
    return {"ok": True}


@app.get("/api/me/consents")
def get_my_consents(user: dict = Depends(auth.get_current_user)):
    prof = db.get_profile(user["id"])
    if prof is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    return {"agree_marketing": bool(prof["agree_marketing"]),
            "agree_openbanking": bool(prof["agree_openbanking"])}


@app.put("/api/me/consents")
def update_my_consents(req: ConsentReq, user: dict = Depends(auth.get_current_user)):
    if not req.agree_openbanking:
        raise HTTPException(status_code=400, detail="오픈뱅킹 동의를 철회하면 서비스를 이용할 수 없습니다.")
    db.set_consents(user["id"], 1 if req.agree_marketing else 0, 1 if req.agree_openbanking else 0)
    return {"ok": True}


@app.get("/api/me/security-events")
def my_security_events(offset: int = 0, limit: int = 30,
                       user: dict = Depends(auth.get_current_user)):
    return {"events": db.list_security_events(offset, limit, username=user["username"])}


@app.get("/api/me/scheduled-transfers")
def my_scheduled_transfers(user: dict = Depends(auth.get_current_user)):
    return {"transfers": db.list_user_scheduled(user["id"])}


@app.get("/api/me/limits")
def my_limits(user: dict = Depends(auth.get_current_user)):
    """읽기전용: 현재 적용 한도(전역 정책) + 오늘 사용액·잔여."""
    cfg = config.load()
    once = int(cfg.get("transfer_limit", TRANSFER_LIMIT))
    daily = int(cfg.get("daily_transfer_limit", DAILY_TRANSFER_LIMIT))
    fee = int(cfg.get("transfer_fee", TRANSFER_FEE))
    today0 = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    used = db.sum_user_transfers_today(db.user_account_nos(user["id"]), today0)
    return {"transfer_limit": once, "daily_transfer_limit": daily, "transfer_fee": fee,
            "used_today": used, "remaining_today": max(0, daily - used)}


@app.get("/api/me/transactions/export")
def export_my_transactions(user: dict = Depends(auth.get_current_user)):
    """전 계좌 통합 거래내역 CSV 다운로드."""
    rows = db.list_user_transactions(user["id"])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["일시", "계좌번호", "은행", "구분", "금액", "거래후잔액", "상대"])
    for r in rows:
        dt = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))
        kind = "입금" if r["type"] == "in" else "출금"
        w.writerow([dt, r["account_no"], r["bank_name"], kind, r["amount"],
                    r["balance_after"] if r["balance_after"] is not None else "", r["counterparty"] or ""])
    csv_bytes = ("﻿" + buf.getvalue()).encode("utf-8")   # BOM: 엑셀 한글 깨짐 방지
    return Response(
        content=csv_bytes, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=transactions.csv"},
    )


# ── 마이페이지: 즐겨찾기 수취인 ──────────────────────────────────────
class FavoriteReq(BaseModel):
    bank_name: str = ""
    account_no: str
    holder_name: str = ""
    nickname: str = ""


@app.get("/api/me/favorites")
def get_favorites(user: dict = Depends(auth.get_current_user)):
    return {"favorites": db.list_favorites(user["id"])}


@app.post("/api/me/favorites")
def add_favorite(req: FavoriteReq, user: dict = Depends(auth.get_current_user)):
    acct_no = req.account_no.strip()
    if not acct_no:
        raise HTTPException(status_code=400, detail="계좌번호를 입력하세요.")
    fid = db.create_favorite(user["id"], req.bank_name.strip(), acct_no,
                             req.holder_name.strip(), req.nickname.strip())
    return {"id": fid}


@app.delete("/api/me/favorites/{fav_id}")
def remove_favorite(fav_id: int, user: dict = Depends(auth.get_current_user)):
    if db.delete_favorite(fav_id, user["id"]):
        return {"ok": True}
    raise HTTPException(status_code=404, detail="즐겨찾기를 찾을 수 없습니다.")


# ── 마이페이지: 회원 탈퇴(소프트 삭제) ───────────────────────────────
@app.post("/api/me/withdraw")
def withdraw(req: WithdrawReq, user: dict = Depends(auth.get_current_user)):
    acct = db.get_user_by_username(user["username"])
    if acct is None or not auth.verify_password(req.password, acct["password_hash"]):
        raise HTTPException(status_code=403, detail="비밀번호가 올바르지 않습니다.")
    if db.user_total_balance(user["id"]) != 0:
        raise HTTPException(status_code=409,
                            detail="잔액이 남은 계좌가 있어 탈퇴할 수 없습니다. 잔액을 먼저 정리하세요.")
    if db.list_user_scheduled(user["id"]):
        raise HTTPException(status_code=409,
                            detail="진행 중인 예약/지연 이체가 있어 탈퇴할 수 없습니다. 먼저 취소하세요.")
    db.deactivate_user(user["id"])
    return {"ok": True}


# ── 로그아웃 상태: 아이디 찾기 / 비밀번호 재설정 (데모 목업 인증) ──────
class FindIdReq(BaseModel):
    name: str
    email: str


class ResetPwReq(BaseModel):
    username: str
    name: str
    phone: str
    new_password: str


def _mask_username(u: str) -> str:
    if len(u) <= 2:
        return u[0] + "*"
    return u[:2] + "*" * (len(u) - 3) + u[-1]


@app.post("/api/find-username")
def find_username(req: FindIdReq):
    """데모: 이름+이메일 일치 시 아이디를 부분 마스킹해 반환(이메일 인증은 프런트에서 선행)."""
    found = db.find_user_by_name_email(req.name.strip(), req.email)
    if found is None:
        raise HTTPException(status_code=404, detail="일치하는 회원 정보를 찾을 수 없습니다.")
    return {"username_masked": _mask_username(found["username"])}


@app.post("/api/reset-password")
def reset_password(req: ResetPwReq):
    """데모: 아이디+이름+전화 일치 시 비밀번호 재설정(실제 서비스는 이메일/SMS 인증 필요)."""
    found = db.find_user_by_identity(req.name.strip(), req.phone)
    if found is None or found["username"] != req.username.strip():
        raise HTTPException(status_code=404, detail="회원 정보가 일치하지 않습니다.")
    if len(req.new_password) < 4:
        raise HTTPException(status_code=400, detail="새 비밀번호는 4자 이상 입력하세요.")
    db.set_password_hash(found["id"], auth.hash_password(req.new_password))
    return {"ok": True}


@app.get("/api/admin/demo-account")
def admin_demo_account(user: dict = Depends(auth.require_admin)):
    return DEMO_ACCOUNT_INFO


# ── Backoffice: 회원관리 ──────────────────────────────────────────────
@app.get("/api/admin/users")
def admin_users(
    offset: int = 0, limit: int = 20, q: str = "", role: str = "",
    user: dict = Depends(auth.require_admin),
):
    return {
        "users": db.list_users(offset, limit, q, role),
        "total": db.count_users(q, role),
        "account_count": db.count_accounts(),
        "total_balance": db.sum_balance(),
    }


@app.get("/api/admin/users/{user_id}")
def admin_user_detail(user_id: int, user: dict = Depends(auth.require_admin)):
    target = db.get_user_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="회원을 찾을 수 없습니다.")
    accounts = db.list_accounts(user_id)
    db.log_admin_access(
        user["username"], user["name"], "view_user_detail",
        target=f"{target['username']}({target.get('name', '')})",
        detail=f"계좌 {len(accounts)}건",
    )
    return {"user": target, "accounts": accounts}


# ── Backoffice: 이체모니터링 ──────────────────────────────────────────
@app.get("/api/admin/transfers")
def admin_transfers(
    offset: int = 0, limit: int = 20, status: str = "", q: str = "",
    user: dict = Depends(auth.require_admin),
):
    transfers = db.list_transfers(offset, limit, status, q)
    db.log_admin_access(
        user["username"], user["name"], "view_transfers",
        target=f"상태={status or '전체'} 검색어={q or '-'}",
        detail=f"{len(transfers)}건 조회",
    )
    return {
        "transfers": transfers,
        "total": db.count_transfers(status, q),
        "summary": db.transfer_summary(),
    }


@app.get("/api/admin/scheduled-transfers")
def admin_scheduled_transfers(user: dict = Depends(auth.require_admin)):
    """예약/지연 이체 대기 큐(예정 시각 오름차순)."""
    return {"transfers": db.list_scheduled_pending()}


@app.get("/api/admin/security-events")
def admin_security_events(
    offset: int = 0, limit: int = 20, event_type: str = "",
    user: dict = Depends(auth.require_admin),
):
    """이체 보안 이벤트(한도초과·비번실패·신규계좌) 로그 + 요약."""
    return {
        "events": db.list_security_events(offset, limit, event_type),
        "total": db.count_security_events(event_type),
        "summary": db.security_event_summary(),
    }


@app.get("/api/admin/access-log")
def admin_access_log(
    offset: int = 0, limit: int = 20, action: str = "",
    user: dict = Depends(auth.require_admin),
):
    """관리자의 개인신용정보(이체내역·회원상세) 열람 로그 + 요약.
    이 엔드포인트 자체는 로깅하지 않는다(자기 참조 방지)."""
    return {
        "logs": db.list_admin_access_log(offset, limit, action),
        "total": db.count_admin_access_log(action),
        "summary": db.admin_access_log_summary(),
    }


# ── Backoffice: 인프라 실시간 지표 (대시보드 스트립 + 성능관리 공용) ────
@app.get("/api/admin/infra-metrics")
def admin_infra_metrics(user: dict = Depends(auth.require_admin)):
    cfg = config.load()
    _ps = get_poller_status()
    return {
        "kafka": infra_metrics.kafka_metrics(),
        "elasticsearch": infra_metrics.elasticsearch_metrics(),
        "phoenix": infra_metrics.phoenix_metrics(),
        "scheduled_poller": infra_metrics.scheduled_poller_metrics(
            _ps["started"], _ps["last_run"], len(db.list_scheduled_pending())
        ),
        "llm_config": {
            "provider": cfg.get("provider") or llm.DEFAULT_PROVIDER,
            "model": cfg.get("default_model", ""),
        },
        "rag_config": {
            "top_k": cfg.get("rag_top_k", 10),
            "cache_enabled": bool(cfg.get("cache_enabled", False)),
        },
    }


# ── Backoffice: AI은행원 설정 (성능관리 탭) ──────────────────────────────
# 답변 스타일(default_style)은 관리자가 고를 수 없다 — 금융 서비스 특성상 항상 llm.DEFAULT_STYLE
# ("정확" 모드)로 고정한다. config.json에 다른 값이 남아있더라도 응답·저장 시 항상 이 값으로 덮어쓴다.
@app.get("/api/admin/chatbot-config")
def get_chatbot_config(user: dict = Depends(auth.require_admin)):
    cfg = config.load()
    return {
        "config": {
            "provider": cfg.get("provider") or llm.DEFAULT_PROVIDER,
            "default_model": cfg.get("default_model", ""),
            "default_style": llm.DEFAULT_STYLE,
            "system_prompt": cfg.get("system_prompt", ""),
            "web_search": bool(cfg.get("web_search", False)),
        },
        "providers": llm.PROVIDERS,
    }


class ChatbotConfigReq(BaseModel):
    provider: str
    default_model: str
    system_prompt: str
    web_search: bool


@app.put("/api/admin/chatbot-config")
def update_chatbot_config(req: ChatbotConfigReq, user: dict = Depends(auth.require_admin)):
    if req.provider not in llm.PROVIDERS:
        raise HTTPException(400, "지원하지 않는 제공자입니다.")
    if req.default_model not in llm.models_for(req.provider):
        raise HTTPException(400, "지원하지 않는 모델입니다.")
    cfg = config.load()
    cfg.update(req.model_dump())
    cfg["default_style"] = llm.DEFAULT_STYLE
    config.save(cfg)
    db.create_chatbot_config_history(
        provider=req.provider,
        default_model=req.default_model,
        default_style=llm.DEFAULT_STYLE,
        system_prompt=req.system_prompt,
        web_search=req.web_search,
        changed_by=user.get("name") or user.get("username") or "",
    )
    return {"ok": True}


@app.get("/api/admin/chatbot-config/history")
def list_chatbot_config_history(offset: int = 0, limit: int = 20, user: dict = Depends(auth.require_admin)):
    history = db.list_chatbot_config_history(offset=offset, limit=limit)
    for h in history:
        h["web_search"] = bool(h["web_search"])
    return {"history": history, "total": db.count_chatbot_config_history()}


@app.post("/api/admin/chatbot-config/history/{history_id}/restore")
def restore_chatbot_config_history(history_id: int, user: dict = Depends(auth.require_admin)):
    entry = db.get_chatbot_config_history_entry(history_id)
    if entry is None:
        raise HTTPException(404, "해당 버전을 찾을 수 없습니다.")
    cfg = config.load()
    cfg.update({
        "provider": entry["provider"],
        "default_model": entry["default_model"],
        "default_style": llm.DEFAULT_STYLE,  # 과거 버전의 스타일이 무엇이었든 되돌리기도 항상 정확 모드로 고정
        "system_prompt": entry["system_prompt"],
        "web_search": bool(entry["web_search"]),
    })
    config.save(cfg)
    db.create_chatbot_config_history(
        provider=entry["provider"],
        default_model=entry["default_model"],
        default_style=llm.DEFAULT_STYLE,
        system_prompt=entry["system_prompt"],
        web_search=bool(entry["web_search"]),
        changed_by=user.get("name") or user.get("username") or "",
    )
    return {"ok": True}


# ── Backoffice: 프롬프트 A/B 테스트 ──────────────────────────────────
# "현재 저장된 프롬프트" vs "지금 편집 중인(아직 저장 안 된) 프롬프트"를 같은 질문·같은(고정)
# 답변 스타일로 비교한다. 답변 스타일은 관리자가 고를 수 없으므로(항상 llm.DEFAULT_STYLE) 여기서도
# 그 값만 쓴다 — 예전처럼 스타일을 바꿔가며 비교하는 기능은 없앴다.
class PromptABTestReq(BaseModel):
    question: str
    provider: str
    model: str
    prompt_a: str
    prompt_b: str


@app.post("/api/admin/prompt-ab-test")
def prompt_ab_test(req: PromptABTestReq, user: dict = Depends(auth.require_admin)):
    if not req.question.strip():
        raise HTTPException(400, "질문을 입력하세요.")
    if req.provider not in llm.PROVIDERS:
        raise HTTPException(400, "지원하지 않는 제공자입니다.")
    if req.model not in llm.models_for(req.provider):
        raise HTTPException(400, "지원하지 않는 모델입니다.")

    cfg = config.load()
    api_key = (cfg.get("openai_api_key") or "").strip()
    if not api_key:
        raise HTTPException(400, "OpenAI API 키가 config.json에 없습니다.")

    def _run_once(prompt: str) -> dict:
        started = time.perf_counter()
        try:
            text = "".join(llm.stream_chat(
                provider=req.provider,
                api_key=api_key,
                model=req.model,
                messages=[{"role": "user", "content": req.question}],
                temperature=llm.TEMP_OPTIONS[llm.DEFAULT_STYLE],
                system_prompt=prompt.strip() or None,
            ))
            ok = True
        except Exception as e:  # 셀별 오류 표시(전체 500 대신)
            text = f"(오류) {e}"
            ok = False
        return {
            "response": text,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "ok": ok,
        }

    # A, B 순차 실행(데모 규모에서 충분)
    return {"a": _run_once(req.prompt_a), "b": _run_once(req.prompt_b)}


# ── AI은행원 답변 평가(좋아요/싫어요) ──────────────────────────────────
class ChatFeedbackReq(BaseModel):
    conversation_id: str
    message_index: int
    rating: str
    reasons: list[str] = []
    comment: str = ""
    question: str = ""
    answer: str = ""


@app.post("/api/chat-feedback")
def create_chat_feedback(req: ChatFeedbackReq):
    if req.rating not in ("up", "down"):
        raise HTTPException(400, "rating은 up 또는 down이어야 합니다.")
    feedback_id = db.create_chat_feedback(
        conversation_id=req.conversation_id,
        message_index=req.message_index,
        rating=req.rating,
        reasons=json.dumps(req.reasons, ensure_ascii=False) if req.reasons else "",
        comment=req.comment,
        question=req.question,
        answer=req.answer,
    )
    return {"id": feedback_id}


@app.get("/api/admin/chat-feedback")
def admin_chat_feedback(
    offset: int = 0, limit: int = 20, rating: str = "",
    user: dict = Depends(auth.require_admin),
):
    items = db.list_chat_feedback(offset, limit, rating)
    return {
        "items": [
            {**item, "reasons": json.loads(item["reasons"]) if item["reasons"] else []}
            for item in items
        ],
        "total": db.count_chat_feedback(rating),
        "summary": db.chat_feedback_summary(),
        "reason_counts": db.chat_feedback_reason_counts(),
    }


# ── Backoffice: 이체 정책(한도/수수료) ────────────────────────────────
@app.get("/api/admin/transfer-policy")
def get_transfer_policy(user: dict = Depends(auth.require_admin)):
    cfg = config.load()
    return {
        "transfer_limit": int(cfg.get("transfer_limit", TRANSFER_LIMIT)),
        "daily_transfer_limit": int(cfg.get("daily_transfer_limit", DAILY_TRANSFER_LIMIT)),
        "transfer_fee": int(cfg.get("transfer_fee", TRANSFER_FEE)),
    }


class TransferPolicyReq(BaseModel):
    transfer_limit: int
    daily_transfer_limit: int
    transfer_fee: int


@app.put("/api/admin/transfer-policy")
def update_transfer_policy(req: TransferPolicyReq, user: dict = Depends(auth.require_admin)):
    if req.transfer_limit <= 0 or req.daily_transfer_limit <= 0 or req.transfer_fee < 0:
        raise HTTPException(400, "한도는 0보다 커야 하고 수수료는 음수가 될 수 없습니다.")
    if req.transfer_limit > req.daily_transfer_limit:
        raise HTTPException(400, "1회 한도는 1일 한도를 넘을 수 없습니다.")
    cfg = config.load()
    cfg.update(req.model_dump())
    config.save(cfg)
    return {"ok": True}


# ── Backoffice: 이용통계 ──────────────────────────────────────────────
@app.get("/api/admin/usage-stats")
def admin_usage_stats(user: dict = Depends(auth.require_admin)):
    return {
        "summary": db.stats_usage_summary(),
        "daily": db.stats_usage_daily(),
        "categories": db.stats_usage_by_category(),
    }


# ── 계좌 조회 ────────────────────────────────────────────────────────
@app.get("/api/accounts")
def get_accounts(user: dict = Depends(auth.get_current_user)):
    return {"accounts": db.list_accounts(user["id"])}


@app.get("/api/accounts/{account_id}/transactions")
def get_transactions(account_id: int, offset: int = 0, limit: int = 50,
                      user: dict = Depends(auth.get_current_user)):
    acc = db.get_account(account_id, user["id"])
    if acc is None:
        raise HTTPException(status_code=404, detail="계좌를 찾을 수 없습니다.")
    return {
        "account": acc,
        "transactions": db.list_transactions(account_id, offset, limit),
        "total": db.count_transactions(account_id),
    }


@app.get("/api/accounts/lookup")
def lookup_account(account_no: str, from_account: str | None = None,
                   user: dict = Depends(auth.get_current_user)):
    """받는 계좌의 예금주·은행 조회 + 예상 수수료 + 신규 수취계좌 여부."""
    dst = db.lookup_account(account_no)
    if dst is None:
        raise HTTPException(status_code=404, detail="조회되지 않는 계좌입니다. 계좌번호를 확인하세요.")
    fee = 0
    if from_account:
        src = db.lookup_account(from_account)
        if src is not None and src["bank_name"] != dst["bank_name"]:
            fee = int(config.load().get("transfer_fee", TRANSFER_FEE))
    return {
        "account_no": dst["account_no"],
        "bank_name": dst["bank_name"],
        "holder_name": dst["holder_name"],
        "fee": fee,
        "is_new_payee": db.is_new_payee(user["id"], dst["account_no"]),
    }


# ── 마이페이지: 계좌 관리(추가/수정/해지) ───────────────────────────
class AccountCreateReq(BaseModel):
    bank_name: str
    account_no: str
    account_holder: str
    nickname: str = ""
    is_primary: bool = False


class AccountUpdateReq(BaseModel):
    nickname: str | None = None
    is_primary: bool | None = None


@app.post("/api/accounts")
def add_account(req: AccountCreateReq, user: dict = Depends(auth.get_current_user)):
    bank_name = req.bank_name.strip()
    account_no = req.account_no.strip()
    if not bank_name or not account_no:
        raise HTTPException(status_code=400, detail="등록할 계좌 정보를 입력하세요.")
    acct = db.get_user_by_username(user["username"])
    if acct is None or req.account_holder.strip() != acct["name"]:
        raise HTTPException(status_code=400, detail="예금주명이 회원 이름과 일치하지 않습니다.")
    if db.lookup_account(account_no) is not None:
        raise HTTPException(status_code=400, detail="이미 등록된 계좌번호입니다.")
    try:
        acc_id = db.create_account(
            user["id"], account_no, bank_name, acct["name"], balance=0,
            nickname=req.nickname.strip(), is_primary=1 if req.is_primary else 0,
        )
    except (sqlite3.IntegrityError, ValueError):
        # libsql은 UNIQUE 제약 위반 시 sqlite3.IntegrityError 대신 ValueError를 던진다
        raise HTTPException(status_code=400, detail="이미 등록된 계좌번호입니다.")
    return {"id": acc_id}


@app.put("/api/accounts/{account_id}")
def edit_account(account_id: int, req: AccountUpdateReq,
                 user: dict = Depends(auth.get_current_user)):
    nickname = req.nickname.strip() if req.nickname is not None else None
    is_primary = 1 if req.is_primary else None
    if not db.update_account(account_id, user["id"], nickname, is_primary):
        raise HTTPException(status_code=404, detail="계좌를 찾을 수 없습니다.")
    return {"ok": True}


@app.delete("/api/accounts/{account_id}")
def close_account(account_id: int, user: dict = Depends(auth.get_current_user)):
    ok, reason = db.delete_account(account_id, user["id"])
    if not ok:
        raise HTTPException(status_code=409, detail=reason)
    return {"ok": True}


# ── 이체 ─────────────────────────────────────────────────────────────
@app.post("/api/transfer")
def transfer(req: TransferReq, user: dict = Depends(auth.get_current_user)):
    # 이체 정책(관리자 설정) — 없으면 모듈 상수를 fallback 으로 사용
    _cfg = config.load()
    transfer_limit = int(_cfg.get("transfer_limit", TRANSFER_LIMIT))
    daily_limit = int(_cfg.get("daily_transfer_limit", DAILY_TRANSFER_LIMIT))
    transfer_fee = int(_cfg.get("transfer_fee", TRANSFER_FEE))

    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="이체 금액이 올바르지 않습니다.")
    if req.amount > transfer_limit:
        db.log_security_event("limit_once", user["username"], req.from_account,
                              req.to_account, req.amount,
                              f"1회 한도 {transfer_limit:,}원 초과")
        raise HTTPException(
            status_code=400,
            detail=f"1회 이체 한도({transfer_limit:,}원)를 초과했습니다.",
        )
    # 출금 계좌가 로그인한 사용자 소유인지 확인
    my = {a["account_no"]: a for a in db.list_accounts(user["id"])}
    src = my.get(req.from_account)
    if src is None:
        raise HTTPException(status_code=400, detail="출금 계좌가 올바르지 않습니다.")

    # 받는 계좌 예금주 조회(필수)
    dst = db.lookup_account(req.to_account)
    if dst is None:
        raise HTTPException(status_code=404, detail="받는 계좌를 조회할 수 없습니다.")
    if req.to_account == req.from_account:
        raise HTTPException(status_code=400, detail="같은 계좌로는 이체할 수 없습니다.")

    fee = 0 if src["bank_name"] == dst["bank_name"] else transfer_fee
    if src["balance"] < req.amount + fee:
        raise HTTPException(status_code=400, detail="잔액이 부족합니다.")

    # 1일 누적 이체 한도(내 계좌들 오늘 completed+pending 합계 + 이번 금액)
    _today0 = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    if db.sum_user_transfers_today(list(my.keys()), _today0) + req.amount > daily_limit:
        db.log_security_event("limit_daily", user["username"], req.from_account,
                              req.to_account, req.amount,
                              f"1일 한도 {daily_limit:,}원 초과")
        raise HTTPException(
            status_code=400,
            detail=f"1일 이체 한도({daily_limit:,}원)를 초과했습니다.",
        )

    # 본인 확인(이체 비밀번호 재인증) — 버튼 클릭만으로 실행되지 않도록 서버가 재검증.
    # 별도 이체 비밀번호(6자리 PIN)로 검증하되, 미설정(레거시/시드) 계정은 로그인 비밀번호로 폴백.
    _acct = db.get_user_by_username(user["username"])
    _tpw_hash = (db.get_transfer_password_hash(user["id"]) or "").strip()
    if _tpw_hash:
        _ok = bool(req.password) and auth.verify_password(req.password, _tpw_hash)
    else:
        _ok = bool(req.password) and _acct is not None and auth.verify_password(req.password, _acct["password_hash"])
    if not _ok:
        db.log_security_event("password_fail", user["username"], req.from_account,
                              req.to_account, req.amount, "이체 비밀번호 재인증 실패")
        raise HTTPException(status_code=403, detail="이체 비밀번호가 올바르지 않습니다. 본인 확인에 실패했습니다.")

    # 예약/지연 이체 판별: 즉시 실행이 아니면 미래 시각에 폴러가 처리
    now = time.time()
    sched_at = None
    sched_status = "pending"
    if req.scheduled_at and req.scheduled_at > now + 30:      # 예약(미래 시각)
        sched_at, sched_status = float(req.scheduled_at), "scheduled"
    elif req.delay_minutes and req.delay_minutes > 0:          # 지연이체(취소 가능)
        sched_at, sched_status = now + req.delay_minutes * 60, "delayed"

    # 신규 수취계좌로의 이체는 감사 로그에 기록(예외 아님, 참고용)
    if db.is_new_payee(user["id"], dst["account_no"]):
        db.log_security_event("new_payee", user["username"], req.from_account,
                              req.to_account, req.amount,
                              f"신규 수취계좌 이체 · 예금주 {dst['holder_name']}")

    transfer_id = db.create_transfer(
        req.from_account, req.to_account, req.amount,
        to_bank=dst["bank_name"], to_holder=dst["holder_name"],
        fee=fee, memo=req.memo, sender_memo=req.sender_memo,
        status=sched_status, scheduled_at=sched_at,
    )

    # 예약/지연이면 지금 발행하지 않고 폴러가 실행 시각에 처리
    if sched_status != "pending":
        return {"transfer_id": transfer_id, "status": sched_status, "fee": fee,
                "scheduled_at": sched_at}

    # 즉시 이체: Kafka로 이체 이벤트 발행(성공하면 워커가 비동기 처리).
    # Kafka가 아예 없거나(KAFKA_DISABLED) 발행이 실패하면 이 요청 안에서
    # process_transfer()를 직접 동기 실행한다 — Kafka 가용성이 이체 성공 여부를
    # 막아선 안 되기 때문. process_transfer()는 status != 'pending'이면 그냥
    # 리턴하는 자체 멱등성 가드가 있어서, 나중에 Kafka가 살아나 같은 메시지를
    # 처리해도 이중 차감되지 않는다. (fail_transfer()를 여기서 호출하면 안 됨 —
    # 그러면 상태가 'failed'로 바뀌어 아래 process_transfer()가 멱등성 가드에
    # 걸려 실제 처리를 건너뛰게 된다.)
    kafka_ok = False
    if not kafka_io.KAFKA_DISABLED:
        try:
            producer = _get_producer()
            producer.send(kafka_io.TOPIC_TRANSFER, {
                "transfer_id": transfer_id,
                "from_account": req.from_account,
                "to_account": req.to_account,
                "amount": req.amount,
            })
            producer.flush(timeout=5)
            kafka_ok = True
        except Exception:
            kafka_ok = False

    if kafka_ok:
        return {"transfer_id": transfer_id, "status": "pending", "fee": fee}

    status = db.process_transfer(transfer_id)
    return {"transfer_id": transfer_id, "status": status, "fee": fee}


@app.post("/api/transfers/{transfer_id}/cancel")
def cancel_transfer(transfer_id: int, user: dict = Depends(auth.get_current_user)):
    """예약/지연 이체를 실행 전 취소."""
    if db.cancel_scheduled(transfer_id, user["id"]):
        return {"ok": True, "status": "canceled"}
    raise HTTPException(status_code=400, detail="취소할 수 없는 이체입니다(이미 처리되었거나 권한 없음).")


@app.get("/api/transfers/{transfer_id}")
def transfer_status(transfer_id: int):
    tr = db.get_transfer(transfer_id)
    if tr is None:
        raise HTTPException(status_code=404, detail="이체 내역을 찾을 수 없습니다.")
    return tr


# ── 이용 통계: 추적 ──────────────────────────────────────────────────
@app.post("/api/track/view")
def track_view(req: ViewReq):
    db.add_usage_event("view", bank=req.bank, product=req.product, category=req.category)
    return {"ok": True}


@app.post("/api/track/search")
def track_search(req: SearchReq):
    for it in req.items:
        db.add_usage_event("search", bank=it.bank, product=it.product, category=it.category)
    return {"ok": True, "count": len(req.items)}


# ── 이용 통계: 집계 ──────────────────────────────────────────────────
@app.get("/api/stats/banks")
def stats_banks(limit: int = 10):
    return {"banks": db.stats_banks(limit=limit)}


@app.get("/api/stats/top-products")
def stats_top_products():
    return {"categories": db.stats_top_products()}


# ── 상품안내: FSS(금융감독원) 실시간 상품 데이터 ──────────────────────
_PRODUCT_CACHE: dict[str, tuple[float, list]] = {}
_PRODUCT_CACHE_TTL = 3600  # 1시간


@app.get("/api/products")
def get_products(category: str):
    if category not in ("예금", "적금", "금리비교", *fss_fetcher.LOAN_CATEGORIES):
        raise HTTPException(400, "지원하지 않는 카테고리입니다.")
    now = time.time()
    cached = _PRODUCT_CACHE.get(category)
    if cached and now - cached[0] < _PRODUCT_CACHE_TTL:
        return {"products": cached[1]}

    auth_key = config.load().get("fss_api_key", "")
    if not auth_key:
        raise HTTPException(503, "FSS API 키가 설정되지 않았습니다.")
    try:
        if category == "금리비교":
            products = fss_fetcher.fetch_category_structured(auth_key, "예금") + \
                fss_fetcher.fetch_category_structured(auth_key, "적금")
        else:
            products = fss_fetcher.fetch_category_structured(auth_key, category)
    except Exception as e:
        raise HTTPException(502, f"FSS API 조회 실패: {e}")

    # 대출은 금리가 낮을수록 유리하므로 오름차순(최저금리 우선), 예금/적금은 내림차순(최고금리 우선)
    if category in fss_fetcher.LOAN_CATEGORIES:
        products.sort(key=lambda p: p["best_rate"] if p["best_rate"] is not None else float("inf"))
    else:
        products.sort(key=lambda p: p["best_rate"] or 0, reverse=True)
    _PRODUCT_CACHE[category] = (now, products)
    return {"products": products}


# ── Backoffice: FSS 데이터 신선도·업데이트 모니터링 ────────────────────
_FSS_DATA_DIR = Path(__file__).resolve().parent.parent / "site" / "data"
_FSS_SNAPSHOT = _FSS_DATA_DIR / "fss_snapshot.json"
_FSS_INGEST = _FSS_DATA_DIR / "fss_ingest.json"


def _fss_key(p: dict) -> str:
    return f"{p['bank']}|{p['product_name']}|{p['category']}"


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _fss_fetch_all(auth_key: str) -> list:
    """예금+적금 구조화 상품(캐시 재사용)."""
    now = time.time()
    cached = _PRODUCT_CACHE.get("금리비교")
    if cached and now - cached[0] < _PRODUCT_CACHE_TTL:
        return cached[1]
    products = fss_fetcher.fetch_category_structured(auth_key, "예금") + \
        fss_fetcher.fetch_category_structured(auth_key, "적금")
    products.sort(key=lambda p: p["best_rate"] or 0, reverse=True)
    _PRODUCT_CACHE["금리비교"] = (now, products)
    return products


@app.get("/api/admin/fss-status")
def admin_fss_status(user: dict = Depends(auth.require_admin)):
    """FSS 키 상태 + 상품 수 + 최신 공시일 + 마지막 색인 + 스냅샷 대비 변경."""
    ingest = _load_json(_FSS_INGEST)
    auth_key = config.load().get("fss_api_key", "")
    if not auth_key:
        return {"status": "down", "detail": "FSS API 키가 설정되지 않았습니다.",
                "products": 0, "by_category": {}, "latest_dcls": None,
                "ingest": ingest, "changes_summary": None, "changes": None}
    try:
        products = _fss_fetch_all(auth_key)
    except Exception as e:
        return {"status": "down", "detail": f"FSS API 조회 실패: {e}",
                "products": 0, "by_category": {}, "latest_dcls": None,
                "ingest": ingest, "changes_summary": None, "changes": None}

    by_cat: dict[str, int] = {}
    for p in products:
        by_cat[p["category"]] = by_cat.get(p["category"], 0) + 1
    dcls = [p.get("dcls_date") for p in products if p.get("dcls_date")]
    latest_dcls = max(dcls) if dcls else None

    # 저장 스냅샷 대비 변경(신규/금리변경/삭제)
    snap = _load_json(_FSS_SNAPSHOT) or {}
    cur = {_fss_key(p): {"best_rate": p.get("best_rate"), "dcls_date": p.get("dcls_date")}
           for p in products}
    new, rate_changed, removed = [], [], []
    for k, v in cur.items():
        if k not in snap:
            new.append(k)
        elif snap[k].get("best_rate") != v["best_rate"]:
            rate_changed.append({"key": k, "old": snap[k].get("best_rate"), "new": v["best_rate"]})
    for k in snap:
        if k not in cur:
            removed.append(k)

    return {
        "status": "ok",
        "detail": f"FSS 연결 정상 · 상품 {len(products)}건",
        "products": len(products),
        "by_category": by_cat,
        "latest_dcls": latest_dcls,
        "ingest": ingest,
        "changes_summary": {"new": len(new), "rate_changed": len(rate_changed),
                            "removed": len(removed), "has_snapshot": bool(snap)},
        "changes": {"new": new[:20], "rate_changed": rate_changed[:20], "removed": removed[:20]},
    }


@app.post("/api/admin/fss-status/snapshot")
def admin_fss_snapshot(user: dict = Depends(auth.require_admin)):
    """현재 상품 상태를 기준 스냅샷으로 저장(이후 변경 감지의 기준)."""
    auth_key = config.load().get("fss_api_key", "")
    if not auth_key:
        raise HTTPException(503, "FSS API 키가 설정되지 않았습니다.")
    try:
        products = _fss_fetch_all(auth_key)
    except Exception as e:
        raise HTTPException(502, f"FSS API 조회 실패: {e}")
    snap = {_fss_key(p): {"best_rate": p.get("best_rate"), "dcls_date": p.get("dcls_date")}
            for p in products}
    _FSS_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _FSS_SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "count": len(snap)}


# ── 고객센터: 공지사항 / FAQ / 서식·약관·설명서 (공개) ────────────────
@app.get("/api/notices")
def get_notices(offset: int = 0, limit: int = 20, q: str = ""):
    return {"notices": db.list_notices(offset, limit, q), "total": db.count_notices(q)}


@app.get("/api/faqs")
def get_faqs(offset: int = 0, limit: int = 20, q: str = ""):
    return {"faqs": db.list_faqs(offset, limit, q), "total": db.count_faqs(q)}


@app.get("/api/documents")
def get_documents(offset: int = 0, limit: int = 20, q: str = ""):
    return {"documents": db.list_documents(offset, limit, q), "total": db.count_documents(q)}


# ── Backoffice: 공지사항·FAQ·서식자료 관리 ─────────────────────────────
class NoticeReq(BaseModel):
    title: str
    content: str


@app.post("/api/admin/notices")
def admin_create_notice(req: NoticeReq, user: dict = Depends(auth.require_admin)):
    return {"id": db.create_notice(req.title, req.content)}


@app.put("/api/admin/notices/{notice_id}")
def admin_update_notice(notice_id: int, req: NoticeReq, user: dict = Depends(auth.require_admin)):
    db.update_notice(notice_id, req.title, req.content)
    return {"ok": True}


@app.delete("/api/admin/notices/{notice_id}")
def admin_delete_notice(notice_id: int, user: dict = Depends(auth.require_admin)):
    db.delete_notice(notice_id)
    return {"ok": True}


class FaqReq(BaseModel):
    question: str
    answer: str


@app.post("/api/admin/faqs")
def admin_create_faq(req: FaqReq, user: dict = Depends(auth.require_admin)):
    return {"id": db.create_faq(req.question, req.answer)}


@app.put("/api/admin/faqs/{faq_id}")
def admin_update_faq(faq_id: int, req: FaqReq, user: dict = Depends(auth.require_admin)):
    db.update_faq(faq_id, req.question, req.answer)
    return {"ok": True}


@app.delete("/api/admin/faqs/{faq_id}")
def admin_delete_faq(faq_id: int, user: dict = Depends(auth.require_admin)):
    db.delete_faq(faq_id)
    return {"ok": True}


class DocumentReq(BaseModel):
    title: str
    category: str
    description: str = ""


@app.post("/api/admin/documents")
def admin_create_document(req: DocumentReq, user: dict = Depends(auth.require_admin)):
    if req.category not in ("약관", "서식", "설명서"):
        raise HTTPException(400, "지원하지 않는 구분입니다.")
    return {"id": db.create_document(req.title, req.category, req.description)}


@app.put("/api/admin/documents/{document_id}")
def admin_update_document(document_id: int, req: DocumentReq, user: dict = Depends(auth.require_admin)):
    if req.category not in ("약관", "서식", "설명서"):
        raise HTTPException(400, "지원하지 않는 구분입니다.")
    db.update_document(document_id, req.title, req.category, req.description)
    return {"ok": True}


@app.delete("/api/admin/documents/{document_id}")
def admin_delete_document(document_id: int, user: dict = Depends(auth.require_admin)):
    db.delete_document(document_id)
    return {"ok": True}


# ── Backoffice: 인프라 설정 조회(읽기 전용, API 키 제외) ────────────────
@app.get("/api/admin/infra-config")
def admin_infra_config(user: dict = Depends(auth.require_admin)):
    cfg = config.load()
    return {
        "openai_key_set": bool(cfg.get("openai_api_key")),
        "fss_key_set": bool(cfg.get("fss_api_key")),
        "chatbot_provider": cfg.get("provider") or llm.DEFAULT_PROVIDER,
        "chatbot_model": cfg.get("default_model", ""),
        "chatbot_web_search": bool(cfg.get("web_search", False)),
        "cache_enabled": bool(cfg.get("cache_enabled", False)),
        "rag_top_k": cfg.get("rag_top_k"),
        "cache_threshold": cfg.get("cache_threshold"),
        "redis_ttl": cfg.get("redis_ttl"),
        "es_host": cfg.get("es_host"),
        "redis_host": cfg.get("redis_host"),
        "redis_port": cfg.get("redis_port"),
    }


# ── 고객센터: 문의하기 (로그인 필수) ───────────────────────────────────
@app.post("/api/inquiries")
def create_inquiry(req: InquiryReq, user: dict = Depends(auth.get_current_user)):
    inquiry_id = db.create_inquiry(user["id"], req.title, req.content)
    return {"id": inquiry_id}


@app.get("/api/inquiries")
def get_inquiries(offset: int = 0, limit: int = 20, user: dict = Depends(auth.get_current_user)):
    return {
        "inquiries": db.list_inquiries(user["id"], offset, limit),
        "total": db.count_inquiries(user["id"]),
    }


# ── Backoffice: 문의내역 전체 조회 (읽기 전용 — inquiries 테이블에 답변 컬럼이
# 없어 응답 기능은 없음) ────────────────────────────────────────────────
@app.get("/api/admin/inquiries")
def admin_get_inquiries(offset: int = 0, limit: int = 20, q: str = "", user: dict = Depends(auth.require_admin)):
    return {
        "inquiries": db.list_all_inquiries(offset, limit, q),
        "total": db.count_all_inquiries(q),
    }


# ── 홈 배너 (공개) ───────────────────────────────────────────────────
@app.get("/api/banners")
def get_banners():
    return {"banners": db.list_active_banners()}


BANNER_IMAGE_DIR = SITE_DIR / "img" / "banners"
BANNER_IMAGE_EXTS = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                     "image/webp": ".webp", "image/svg+xml": ".svg"}
BANNER_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5MB


def _validate_banner_link(link_type: str) -> None:
    if link_type not in ("none", "notice", "event", "special_product"):
        raise HTTPException(400, "지원하지 않는 연결 대상입니다.")


def _save_banner_image(image: UploadFile) -> str:
    """배너 이미지를 site/img/banners/에 저장하고 공개 경로를 반환한다.

    클라이언트가 보낸 원본 파일명은 절대 그대로 쓰지 않고(경로 조작 방지),
    content-type 기준으로 생성한 임의 파일명만 사용한다.
    """
    ext = BANNER_IMAGE_EXTS.get(image.content_type)
    if ext is None:
        raise HTTPException(400, "이미지 파일(jpg/png/gif/webp/svg)만 업로드할 수 있습니다.")
    data = image.file.read()
    if len(data) > BANNER_IMAGE_MAX_BYTES:
        raise HTTPException(400, "이미지 파일은 5MB 이하만 업로드할 수 있습니다.")
    BANNER_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    (BANNER_IMAGE_DIR / filename).write_bytes(data)
    return f"/img/banners/{filename}"


@app.get("/api/admin/banners")
def admin_get_banners(offset: int = 0, limit: int = 20, q: str = "", user: dict = Depends(auth.require_admin)):
    return {"banners": db.list_banners(offset, limit, q), "total": db.count_banners(q)}


@app.post("/api/admin/banners")
def admin_create_banner(
    title: str = Form(...), subtitle: str = Form(""), link_type: str = Form("none"),
    link_id: int | None = Form(None), sort_order: int = Form(0), is_active: bool = Form(True),
    image: UploadFile = File(...), user: dict = Depends(auth.require_admin),
):
    _validate_banner_link(link_type)
    image_path = _save_banner_image(image)
    banner_id = db.create_banner(title, subtitle, image_path, link_type, link_id, sort_order, int(is_active))
    return {"id": banner_id}


@app.put("/api/admin/banners/{banner_id}")
def admin_update_banner(
    banner_id: int, title: str = Form(...), subtitle: str = Form(""), link_type: str = Form("none"),
    link_id: int | None = Form(None), sort_order: int = Form(0), is_active: bool = Form(True),
    image: UploadFile | None = File(None), user: dict = Depends(auth.require_admin),
):
    _validate_banner_link(link_type)
    existing = db.get_banner(banner_id)
    if existing is None:
        raise HTTPException(404, "배너를 찾을 수 없습니다.")
    image_path = _save_banner_image(image) if image is not None and image.filename else existing["image_path"]
    db.update_banner(banner_id, title, subtitle, image_path, link_type, link_id, sort_order, int(is_active))
    return {"ok": True}


@app.delete("/api/admin/banners/{banner_id}")
def admin_delete_banner(banner_id: int, user: dict = Depends(auth.require_admin)):
    db.delete_banner(banner_id)
    return {"ok": True}


# ── 상품안내: 특별상품 (공개 조회 + 관리자 CRUD) ─────────────────────────
@app.get("/api/special-products")
def get_special_products(offset: int = 0, limit: int = 20, q: str = ""):
    return {
        "special_products": db.list_special_products(offset, limit, q),
        "total": db.count_special_products(q),
    }


class SpecialProductReq(BaseModel):
    title: str
    bank_name: str = ""
    rate_text: str = ""
    description: str = ""
    badge: str = ""
    sort_order: int = 0


@app.post("/api/admin/special-products")
def admin_create_special_product(req: SpecialProductReq, user: dict = Depends(auth.require_admin)):
    product_id = db.create_special_product(req.title, req.bank_name, req.rate_text,
                                            req.description, req.badge, req.sort_order)
    return {"id": product_id}


@app.put("/api/admin/special-products/{product_id}")
def admin_update_special_product(product_id: int, req: SpecialProductReq,
                                  user: dict = Depends(auth.require_admin)):
    db.update_special_product(product_id, req.title, req.bank_name, req.rate_text,
                               req.description, req.badge, req.sort_order)
    return {"ok": True}


@app.delete("/api/admin/special-products/{product_id}")
def admin_delete_special_product(product_id: int, user: dict = Depends(auth.require_admin)):
    db.delete_special_product(product_id)
    return {"ok": True}


# ── 고객센터: 이벤트 (공개 조회 + 응모 + 관리자 CRUD/추첨) ────────────────
def _mask_name(name: str) -> str:
    """당첨자 발표는 전체 공개 게시물이라 실명을 그대로 노출하지 않고 가운데를 가린다."""
    name = name or "익명"
    if len(name) <= 2:
        return name[0] + "*"
    return name[0] + "*" * (len(name) - 2) + name[-1]


@app.get("/api/events")
def get_events(offset: int = 0, limit: int = 20, q: str = ""):
    return {"events": db.list_events(offset, limit, q), "total": db.count_events(q)}


@app.get("/api/events/{event_id}")
def get_event_detail(event_id: int):
    event = db.get_event(event_id)
    if event is None:
        raise HTTPException(404, "이벤트를 찾을 수 없습니다.")
    return event


@app.get("/api/events/{event_id}/my-status")
def get_event_my_status(event_id: int, user: dict = Depends(auth.get_current_user)):
    entry = db.get_event_entry(event_id, user["id"])
    return {"entered": entry is not None, "is_winner": bool(entry and entry["is_winner"])}


@app.post("/api/events/{event_id}/enter")
def enter_event(event_id: int, user: dict = Depends(auth.get_current_user)):
    event = db.get_event(event_id)
    if event is None:
        raise HTTPException(404, "이벤트를 찾을 수 없습니다.")
    if event["end_at"] < time.time():
        raise HTTPException(400, "응모 기간이 종료된 이벤트입니다.")
    try:
        db.create_event_entry(event_id, user["id"])
    except (sqlite3.IntegrityError, ValueError):
        # libsql은 UNIQUE 제약 위반 시 sqlite3.IntegrityError 대신 ValueError를 던진다
        raise HTTPException(400, "이미 응모하셨습니다.")
    return {"ok": True}


class EventReq(BaseModel):
    title: str
    content: str = ""
    start_at: float
    end_at: float
    is_drawing: bool = False
    winner_count: int = 0


@app.get("/api/admin/events/{event_id}/entries")
def admin_get_event_entries(event_id: int, offset: int = 0, limit: int = 50,
                             user: dict = Depends(auth.require_admin)):
    return {
        "entries": db.list_event_entries(event_id, offset, limit),
        "total": db.count_event_entries(event_id),
    }


@app.post("/api/admin/events/{event_id}/draw")
def admin_draw_event(event_id: int, user: dict = Depends(auth.require_admin)):
    event = db.get_event(event_id)
    if event is None:
        raise HTTPException(404, "이벤트를 찾을 수 없습니다.")
    if not event["is_drawing"]:
        raise HTTPException(400, "추첨형 이벤트가 아닙니다.")
    already_drawn = bool(event["drawn_at"])
    winners = db.draw_event_winners(event_id, event["winner_count"])
    if not already_drawn:
        # 이벤트 원문 게시글과 당첨자 발표는 실제 이벤트 운영처럼 별도 게시글로 분리한다
        # (동일 글 안에 응모 안내와 당첨 결과가 섞이면 나중에 온 방문자가 헷갈리기 쉬움).
        names = [_mask_name(w["name"] or w["username"]) for w in winners]
        content = (
            f"'{event['title']}' 이벤트 추첨 결과를 발표합니다.\n\n당첨자: {', '.join(names)}"
            if names else
            f"'{event['title']}' 이벤트는 응모자가 없어 당첨자를 선정하지 못했습니다."
        )
        now = time.time()
        db.create_event(
            title=f"[당첨자 발표] {event['title']}", content=content,
            start_at=now, end_at=event["end_at"], is_drawing=0, winner_count=0,
        )
    return {"winners": winners}


@app.post("/api/admin/events")
def admin_create_event(req: EventReq, user: dict = Depends(auth.require_admin)):
    event_id = db.create_event(req.title, req.content, req.start_at, req.end_at,
                                int(req.is_drawing), req.winner_count)
    return {"id": event_id}


@app.put("/api/admin/events/{event_id}")
def admin_update_event(event_id: int, req: EventReq, user: dict = Depends(auth.require_admin)):
    db.update_event(event_id, req.title, req.content, req.start_at, req.end_at,
                     int(req.is_drawing), req.winner_count)
    return {"ok": True}


@app.delete("/api/admin/events/{event_id}")
def admin_delete_event(event_id: int, user: dict = Depends(auth.require_admin)):
    db.delete_event(event_id)
    return {"ok": True}


# site/js/main.js는 정적 파일이라 서버 템플릿 변수를 못 쓴다. AI은행원(Streamlit) iframe
# 주소를 배포 환경마다 다르게 넣을 수 있도록, main.js가 로드되기 전에 이 작은 동적 스크립트로
# window.CHAT_BASE_URL을 먼저 심어둔다. CHAT_BASE_URL 환경변수 미설정 시 빈 문자열 반환 →
# main.js가 기존과 동일하게 http://localhost:8501로 폴백(로컬 개발 동작 변화 없음).
@app.get("/js/env-config.js")
def env_config_js():
    chat_base_url = os.environ.get("CHAT_BASE_URL", "")
    return Response(
        content=f"window.CHAT_BASE_URL = {json.dumps(chat_base_url)};",
        media_type="application/javascript",
    )


# ── AI은행원 깨우기 ──────────────────────────────────────────────────
# AI은행원은 Streamlit Community Cloud 무료 티어에 있고, 그 티어는 12시간 무트래픽이면
# 앱을 재운다. 잠든 상태로 접속하면 챗봇 대신 "Zzzz — 깨울까요?" 화면과 버튼이 뜬다.
# 방문자에게 그 버튼을 누르게 하지 않으려고, 사이트가 iframe을 붙이기 전에 여기로 먼저 깨운다.
#
# 왜 브라우저에서 Streamlit Cloud API를 직접 못 부르나: 그 API에는 Access-Control-Allow-Origin
# 헤더가 없어서 cross-origin fetch가 CORS로 막힌다(2026-09-03 실측). 그래서 서버가 대신 부른다.
#
# 깨우기 절차 — Streamlit Cloud 프런트엔드 번들(states-*.js / schemas-*.js)을 읽어 확인:
#   1) GET  /api/v2/app/status  → 본문 .status로 상태 판별 + 쿠키(_streamlit_csrf)와
#                                 응답 헤더 x-csrf-token을 여기서 한 번에 받는다
#   2) POST /api/v2/app/resume  → 그 쿠키 + x-csrf-token 헤더 필요. 성공 시 204(멱등).
# 쿠키나 CSRF 헤더 중 하나라도 빠지면 403이 난다.
#
# 앱 루트(GET /)를 먼저 치지 않는다: 그건 뷰어 인증 리다이렉트 체인이라 느리고(실측 17초,
# 쿠키 자 없이는 리다이렉트 루프), 어차피 status 호출이 쿠키·CSRF를 다 준다.
_STREAMLIT_RUNNING = 5          # Streamlit Cloud AppStatus: RUNNING=5, IS_SHUTDOWN(절전)=12
_WAKE_POLL_SECONDS = 3
_WAKE_TIMEOUT_SECONDS = 75      # 실측 기동 시간 ~45초


def _streamlit_status(sess: requests.Session, base: str) -> tuple[int, str]:
    """(status, csrf_token)을 돌려준다. status는 위 enum 값."""
    r = sess.get(f"{base}/api/v2/app/status", timeout=10)
    r.raise_for_status()
    return int(r.json().get("status", -1)), r.headers.get("x-csrf-token", "")


@app.post("/api/chat/wake")
def wake_chat_app():
    # 환경변수는 호출 시점에 읽는다(reset-demo와 같은 이유 — 나중에 넣은 값도 반영되도록).
    base = os.environ.get("CHAT_BASE_URL", "").strip().rstrip("/")
    if not base:
        # 로컬 개발: 챗봇을 localhost:8501에 직접 띄우므로 깨울 대상이 없다.
        return {"ready": True, "skipped": True}

    try:
        sess = requests.Session()
        status, csrf = _streamlit_status(sess, base)   # 쿠키·CSRF도 이 호출에서 함께 받는다
        if status == _STREAMLIT_RUNNING:
            return {"ready": True, "status": status}

        sess.post(f"{base}/api/v2/app/resume", timeout=15,
                  headers={"x-csrf-token": csrf})

        deadline = time.time() + _WAKE_TIMEOUT_SECONDS
        while time.time() < deadline:
            time.sleep(_WAKE_POLL_SECONDS)
            status, _ = _streamlit_status(sess, base)
            if status == _STREAMLIT_RUNNING:
                return {"ready": True, "status": status}
        return {"ready": False, "status": status}
    except Exception as e:
        # 외부 서비스 실패가 사이트를 막지 않게 한다(이 저장소 공통 방침).
        # 사이트는 ready=False여도 기존과 동일하게 iframe을 붙인다.
        print(f"[wake_chat_app] AI은행원 깨우기 실패: {e}")
        return {"ready": False, "error": str(e)[:200]}


# ── 공개 데모 데이터 리셋 ────────────────────────────────────────────
# 데모는 계정을 여럿이 공유해서, 방문자가 이체할수록 잔액이 줄고 결국 잔액 부족으로
# 이체가 실패한다. GitHub Actions 가 매일 이 엔드포인트를 호출해 시드 직후 상태로
# 되돌린다. /api/admin/* 이 아니라 /api/maintenance/* 라서 DEMO_READONLY 가드에
# 걸리지 않는다(가드는 백오피스 쓰기만 막는 것이 목적).
#
# 보호: DEMO_RESET_TOKEN 환경변수와 X-Reset-Token 헤더가 일치해야 한다.
# 환경변수가 없으면 기능 자체를 비활성화한다(로컬에서는 그냥 seed_bank.py 를 쓰면 된다).
@app.post("/api/maintenance/reset-demo")
def reset_demo_data(x_reset_token: str = Header(default="")):
    # 환경변수는 호출 시점에 읽는다 — 모듈 로드 시점에 읽어두면 배포 환경에서
    # 값을 나중에 넣었을 때 반영되지 않는다.
    expected = os.environ.get("DEMO_RESET_TOKEN", "").strip()
    if not expected:
        raise HTTPException(404, "Not Found")          # 미설정 = 기능 없음
    if not hmac.compare_digest(x_reset_token.strip(), expected):
        raise HTTPException(403, "리셋 토큰이 올바르지 않습니다.")

    import seed_bank                                    # 시드 값의 단일 출처
    result = seed_bank.reset_demo_data()
    print(f"[reset-demo] 계좌 {len(result['accounts_changed'])}개 복구 · "
          f"거래내역 {result['transactions_reseeded']}건 재시드 · "
          f"이체 {result['transfers_removed']}건 삭제")
    return result


# ── 스키마 반영 (배포 환경 전용, 수동) ────────────────────────────────
# _startup() 이 배포 환경에서 init_db() 를 건너뛰기 때문에(위 주석 참고), 스키마나
# ALTER 마이그레이션을 추가한 뒤에는 배포 후 이 엔드포인트를 한 번 호출해야 반영된다.
#
#   curl -X POST https://<도메인>/api/maintenance/init-db -H "X-Reset-Token: <토큰>"
#
# init_db() 는 CREATE TABLE IF NOT EXISTS + "이미 있으면 무시하는" ALTER 루프라 여러 번
# 호출해도 안전하다. 보호 방식은 위 reset-demo 와 같은 토큰을 그대로 쓴다(운영 토큰을
# 하나로 유지 — 새 토큰을 늘리지 않는다).
@app.post("/api/maintenance/init-db")
def maintenance_init_db(x_reset_token: str = Header(default="")):
    expected = os.environ.get("DEMO_RESET_TOKEN", "").strip()
    if not expected:
        raise HTTPException(404, "Not Found")          # 미설정 = 기능 없음
    if not hmac.compare_digest(x_reset_token.strip(), expected):
        raise HTTPException(403, "리셋 토큰이 올바르지 않습니다.")

    started = time.time()
    db.init_db()
    elapsed = time.time() - started
    print(f"[init-db] 스키마 반영 완료 · {elapsed:.1f}s")
    return {"ok": True, "elapsed_seconds": round(elapsed, 1)}


# ── 정적 사이트 서빙 (마지막에 마운트: /api 라우트가 우선) ───────────
app.mount("/", StaticFiles(directory=str(SITE_DIR), html=True), name="site")
