"""FastAPI 백엔드: 정적 사이트 서빙 + 은행 데모 REST API + Kafka 이체 프로듀서.

실행: .venv/bin/uvicorn backend.app:app --port 8000
- 정적 사이트(site/)를 함께 서빙하므로 python -m http.server 는 불필요.
- 이체는 Kafka(transfer-requests 토픽)로 발행 → transfer_consumer.py 가 처리.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import fss_fetcher
import llm
from backend import auth, db, health, infra_metrics, kafka_io

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


@app.on_event("startup")
def _startup() -> None:
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
    name: str
    phone: str = ""
    email: str = ""
    bank_name: str = ""
    account_no: str = ""
    account_holder: str = ""
    nickname: str = ""
    is_primary: bool = True
    agree_openbanking: bool = False


class InquiryReq(BaseModel):
    title: str
    content: str


class LoginReq(BaseModel):
    username: str
    password: str


# ── 인증 ─────────────────────────────────────────────────────────────
# 관리자가 방문자 입장에서 계좌조회·거래내역·이체·AI챗봇 등 사이트 기능을
# 테스트할 때 쓰는 데모 계정(seed_bank.py에서 생성). 고정값이라 여기서만 노출.
DEMO_ACCOUNT_INFO = {
    "username": "demo",
    "password": "demo1234",
    "note": "계좌조회·거래내역·이체·AI챗봇 등 방문자 기능 테스트용 계정입니다.",
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
    # 계좌 중복은 회원 생성 전에 먼저 확인(중간 실패로 계정만 생성되는 것을 방지).
    # lookup_account는 숫자만 비교하므로 대시 표기 차이까지 잡아낸다.
    if db.lookup_account(account_no) is not None:
        raise HTTPException(status_code=400, detail="이미 등록된 계좌번호입니다.")

    password_hash = auth.hash_password(req.password)
    user_id = db.create_user(
        username, password_hash, name=name, phone=phone_digits, email=req.email.strip(),
    )
    try:
        db.create_account(
            user_id, account_no, bank_name, name, balance=0,
            nickname=req.nickname.strip(), is_primary=1 if req.is_primary else 0,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="이미 등록된 계좌번호입니다.")

    user = db.get_user_by_username(username)
    token = auth.make_token(user)
    return {"token": token, "username": user["username"], "name": user["name"], "role": user["role"]}


@app.post("/api/login")
def login(req: LoginReq):
    user = db.get_user_by_username(req.username.strip())
    if user is None or not user["password_hash"] or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    token = auth.make_token(user)
    return {"token": token, "username": user["username"], "name": user["name"], "role": user["role"]}


@app.get("/api/me")
def me(user: dict = Depends(auth.get_current_user)):
    return user


@app.get("/api/admin/demo-account")
def admin_demo_account(user: dict = Depends(auth.require_admin)):
    return DEMO_ACCOUNT_INFO


# ── Backoffice: 회원관리 ──────────────────────────────────────────────
@app.get("/api/admin/users")
def admin_users(
    offset: int = 0, limit: int = 20, q: str = "",
    user: dict = Depends(auth.require_admin),
):
    return {
        "users": db.list_users(offset, limit, q),
        "total": db.count_users(q),
        "account_count": db.count_accounts(),
        "total_balance": db.sum_balance(),
    }


# ── Backoffice: 이체모니터링 ──────────────────────────────────────────
@app.get("/api/admin/transfers")
def admin_transfers(
    offset: int = 0, limit: int = 20, status: str = "",
    user: dict = Depends(auth.require_admin),
):
    return {
        "transfers": db.list_transfers(offset, limit, status),
        "total": db.count_transfers(status),
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
        "summary": db.security_event_summary(),
    }


# ── Backoffice: 성능관리 ──────────────────────────────────────────────
@app.get("/api/admin/health")
def admin_health(user: dict = Depends(auth.require_admin)):
    return {"checks": health.check_all()}


# ── Backoffice: 대시보드 인프라 실시간 지표 ────────────────────────────
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


# ── Backoffice: AI챗봇 설정 (성능관리 탭) ──────────────────────────────
@app.get("/api/admin/chatbot-config")
def get_chatbot_config(user: dict = Depends(auth.require_admin)):
    cfg = config.load()
    return {
        "config": {
            "provider": cfg.get("provider") or llm.DEFAULT_PROVIDER,
            "default_model": cfg.get("default_model", ""),
            "default_style": cfg.get("default_style", list(llm.TEMP_OPTIONS.keys())[1]),
            "system_prompt": cfg.get("system_prompt", ""),
            "web_search": bool(cfg.get("web_search", False)),
        },
        "providers": llm.PROVIDERS,
        "styles": list(llm.TEMP_OPTIONS.keys()),
    }


class ChatbotConfigReq(BaseModel):
    provider: str
    default_model: str
    default_style: str
    system_prompt: str
    web_search: bool


@app.put("/api/admin/chatbot-config")
def update_chatbot_config(req: ChatbotConfigReq, user: dict = Depends(auth.require_admin)):
    if req.provider not in llm.PROVIDERS:
        raise HTTPException(400, "지원하지 않는 제공자입니다.")
    if req.default_model not in llm.models_for(req.provider):
        raise HTTPException(400, "지원하지 않는 모델입니다.")
    if req.default_style not in llm.TEMP_OPTIONS:
        raise HTTPException(400, "지원하지 않는 답변 스타일입니다.")
    cfg = config.load()
    cfg.update(req.model_dump())
    config.save(cfg)
    return {"ok": True}


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
    return {"summary": db.stats_usage_summary(), "daily": db.stats_usage_daily()}


# ── 계좌 조회 ────────────────────────────────────────────────────────
@app.get("/api/accounts")
def get_accounts(user: dict = Depends(auth.get_current_user)):
    return {"accounts": db.list_accounts(user["id"])}


@app.get("/api/accounts/{account_id}/transactions")
def get_transactions(account_id: int, user: dict = Depends(auth.get_current_user)):
    acc = db.get_account(account_id, user["id"])
    if acc is None:
        raise HTTPException(status_code=404, detail="계좌를 찾을 수 없습니다.")
    return {"account": acc, "transactions": db.list_transactions(account_id)}


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

    # 본인 확인(비밀번호 재인증) — 버튼 클릭만으로 실행되지 않도록 서버가 재검증
    # get_current_user는 password_hash를 주지 않으므로 사용자 레코드를 다시 조회
    _acct = db.get_user_by_username(user["username"])
    if not req.password or _acct is None or not auth.verify_password(req.password, _acct["password_hash"]):
        db.log_security_event("password_fail", user["username"], req.from_account,
                              req.to_account, req.amount, "비밀번호 재인증 실패")
        raise HTTPException(status_code=401, detail="비밀번호가 올바르지 않습니다. 본인 확인에 실패했습니다.")

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

    # 즉시 이체: Kafka로 이체 이벤트 발행
    try:
        producer = _get_producer()
        producer.send(kafka_io.TOPIC_TRANSFER, {
            "transfer_id": transfer_id,
            "from_account": req.from_account,
            "to_account": req.to_account,
            "amount": req.amount,
        })
        producer.flush(timeout=5)
    except Exception as e:
        db.fail_transfer(transfer_id, f"이벤트 발행 실패(Kafka 미기동?): {e}")
        raise HTTPException(
            status_code=503,
            detail="이체 처리 시스템(Kafka)에 연결할 수 없습니다. 잠시 후 다시 시도하세요.",
        )

    return {"transfer_id": transfer_id, "status": "pending", "fee": fee}


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
    if category not in ("예금", "적금", "금리비교"):
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

    products.sort(key=lambda p: p["best_rate"] or 0, reverse=True)
    _PRODUCT_CACHE[category] = (now, products)
    return {"products": products}


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


@app.delete("/api/admin/documents/{document_id}")
def admin_delete_document(document_id: int, user: dict = Depends(auth.require_admin)):
    db.delete_document(document_id)
    return {"ok": True}


# ── Backoffice: 인프라 설정 조회(읽기 전용, API 키 제외) ────────────────
@app.get("/api/admin/infra-config")
def admin_infra_config(user: dict = Depends(auth.require_admin)):
    cfg = config.load()
    return {
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


# ── 정적 사이트 서빙 (마지막에 마운트: /api 라우트가 우선) ───────────
app.mount("/", StaticFiles(directory=str(SITE_DIR), html=True), name="site")
