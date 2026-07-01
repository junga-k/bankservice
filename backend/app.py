"""FastAPI 백엔드: 정적 사이트 서빙 + 은행 데모 REST API + Kafka 이체 프로듀서.

실행: .venv/bin/uvicorn backend.app:app --port 8000
- 정적 사이트(site/)를 함께 서빙하므로 python -m http.server 는 불필요.
- 이체는 Kafka(transfer-requests 토픽)로 발행 → transfer_consumer.py 가 처리.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import auth, db, health, kafka_io

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
# 1회 이체 한도(원)
TRANSFER_LIMIT = 50_000_000


class TransferReq(BaseModel):
    from_account: str
    to_account: str
    amount: int
    memo: str | None = None
    sender_memo: str | None = None


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


class InquiryReq(BaseModel):
    title: str
    content: str


class LoginReq(BaseModel):
    username: str
    password: str


# ── 인증 ─────────────────────────────────────────────────────────────
# 회원가입 시 자동 개설되는 시작 계좌
SIGNUP_BANK = "카카오뱅크"

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
    password_hash = auth.hash_password(req.password)
    user_id = db.create_user(username, password_hash, name=name)
    db.create_account(
        user_id, f"9000-{user_id:04d}-000001", SIGNUP_BANK, name, balance=0,
    )

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


# ── Backoffice: 성능관리 ──────────────────────────────────────────────
@app.get("/api/admin/health")
def admin_health(user: dict = Depends(auth.require_admin)):
    return {"checks": health.check_all()}


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
def lookup_account(account_no: str, from_account: str | None = None):
    """받는 계좌의 예금주·은행 조회 + 예상 수수료 계산."""
    dst = db.lookup_account(account_no)
    if dst is None:
        raise HTTPException(status_code=404, detail="조회되지 않는 계좌입니다. 계좌번호를 확인하세요.")
    fee = 0
    if from_account:
        src = db.lookup_account(from_account)
        if src is not None and src["bank_name"] != dst["bank_name"]:
            fee = TRANSFER_FEE
    return {
        "account_no": dst["account_no"],
        "bank_name": dst["bank_name"],
        "holder_name": dst["holder_name"],
        "fee": fee,
    }


# ── 이체 ─────────────────────────────────────────────────────────────
@app.post("/api/transfer")
def transfer(req: TransferReq, user: dict = Depends(auth.get_current_user)):
    if req.amount <= 0:
        raise HTTPException(status_code=400, detail="이체 금액이 올바르지 않습니다.")
    if req.amount > TRANSFER_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"1회 이체 한도({TRANSFER_LIMIT:,}원)를 초과했습니다.",
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

    fee = 0 if src["bank_name"] == dst["bank_name"] else TRANSFER_FEE
    if src["balance"] < req.amount + fee:
        raise HTTPException(status_code=400, detail="잔액이 부족합니다.")

    transfer_id = db.create_transfer(
        req.from_account, req.to_account, req.amount,
        to_bank=dst["bank_name"], to_holder=dst["holder_name"],
        fee=fee, memo=req.memo, sender_memo=req.sender_memo,
    )

    # Kafka로 이체 이벤트 발행
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
def stats_banks():
    return {"banks": db.stats_banks()}


@app.get("/api/stats/top-products")
def stats_top_products():
    return {"categories": db.stats_top_products()}


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
