"""은행업무 처리 AI 에이전트 (단일 도구호출 에이전트).

OpenAI function-calling으로 백엔드(:8000) 은행 API를 도구로 호출해 대화로 은행업무를 수행한다.

도구는 도메인별로 그룹화되어 있다(ACCOUNT / TRANSFER / PRODUCT / SUPPORT).
추후 다중에이전트로 전환할 때는 라우터가 도메인을 판별해 해당 그룹만 로드한 뒤
동일한 run_agent() 루프를 재사용하면 된다.

이체는 자동 실행하지 않는다. propose_transfer 도구는 예금주·수수료를 확인한
'이체 제안'만 반환하고, 실제 실행은 UI(app.py)에서 사용자가 확인 버튼을 눌러야 이뤄진다.
"""
from __future__ import annotations

import json
import os

import requests

# 배포 환경(Streamlit Cloud)에서는 원격 백엔드를 가리켜야 하므로 환경변수로 덮어쓸 수 있게 한다.
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
_IS_LOCAL_BACKEND = BACKEND_URL.startswith(("http://localhost", "http://127.0.0.1"))

# 원격 백엔드(Vercel + Turso)는 콜드 스타트와 DB 연결 비용 때문에 로컬보다 훨씬 느리다 —
# 배포 실측상 이체 계열은 웜 상태에서도 ~9초가 걸린다. 로컬 기준 5초를 그대로 쓰면
# 이체 제안(propose_transfer)이 반드시 타임아웃나므로 원격일 때는 기본값을 늘린다.
_TIMEOUT = float(os.environ.get("BACKEND_TIMEOUT", "5" if _IS_LOCAL_BACKEND else "30"))

SYSTEM_PROMPT = (
    "당신은 매치뱅크의 은행업무 AI 상담원입니다. 사용자의 계좌 조회, 거래내역, 이체, "
    "금융상품 추천·비교, 고객지원(공지/FAQ/문의) 요청을 도구를 사용해 처리합니다.\n"
    "- 도구가 필요한 요청은 반드시 도구를 호출해 실제 데이터를 근거로 답하세요. 추측하지 마세요.\n"
    "- 이체는 propose_transfer로 '제안'만 하세요. 실제 실행은 사용자가 화면에서 확인합니다.\n"
    "- 이체 시 from_account/to_account 에는 반드시 실제 계좌번호를 넣으세요. 사용자가 '신한은행 계좌'처럼 "
    "은행명만 말하면 먼저 get_accounts 로 내 계좌 목록을 조회해 해당 은행의 계좌번호를 확인한 뒤 사용하세요.\n"
    "- 출금 계좌를 지정하지 않았어도 채팅에서 먼저 물어보지 말고 from_account를 빈 문자열로 두고 "
    "propose_transfer를 바로 호출하세요. 확인 카드 자체에 출금계좌 선택 드롭다운이 있어서(기본값은 "
    "주계좌), 사용자가 거기서 직접 바꿀 수 있습니다. get_accounts로 계좌 목록을 조회해 '어느 계좌에서 "
    "출금할까요?'처럼 채팅으로 되묻는 것은 금지 — 이러면 확인 카드 자체가 안 뜨고 대화만 늘어집니다.\n"
    "- 이체 제안 시 신규 수취계좌(처음 보내는 계좌)이거나 100만원 이상 고액이면 사용자에게 주의를 환기하세요. "
    "실제 실행은 사용자가 예금주·금액을 확인하고 비밀번호를 입력해 승인해야만 이뤄집니다(자동 실행 금지).\n"
    "- 이체는 확인 카드에서 '즉시/지연(취소 가능)/예약(지정 시각)' 중 선택할 수 있습니다. 사용자가 "
    "'나중에', '예약', '지연' 이체를 원하면 propose_transfer로 제안한 뒤 확인 카드에서 시점을 선택하도록 안내하세요.\n"
    "- 상품 추천·비교 요청에는 반드시 search_products를 호출하고, 그 결과 products 목록(상품안내 "
    "페이지와 동일한 FSS 데이터) 안의 상품만 안내하세요. 목록에 없는 상품명·수치를 지어내지 마세요.\n"
    "- 금융상품 정보는 참고용이며 투자·금융 자문이 아님을 필요 시 안내하세요.\n"
    "- 답변은 한국어로, 금액은 원 단위로 읽기 쉽게 표기하세요."
)


# ── 백엔드 호출 헬퍼 ──────────────────────────────────────────────────
def _auth_headers(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _get(path: str, token: str | None = None, params: dict | None = None,
         timeout: float = _TIMEOUT) -> dict:
    r = requests.get(f"{BACKEND_URL}{path}", headers=_auth_headers(token),
                     params=params, timeout=timeout)
    if not r.ok:
        return {"error": _detail(r)}
    return r.json()


def _post(path: str, token: str | None, body: dict) -> dict:
    r = requests.post(f"{BACKEND_URL}{path}", headers=_auth_headers(token),
                      json=body, timeout=_TIMEOUT)
    if not r.ok:
        return {"error": _detail(r)}
    return r.json()


def _detail(resp) -> str:
    try:
        return resp.json().get("detail", f"요청 실패({resp.status_code})")
    except Exception:
        return f"요청 실패({resp.status_code})"


# ── 도구 구현 ────────────────────────────────────────────────────────
def _resolve_account_id(account_no: str, token: str) -> int | None:
    """계좌번호(대시 무관)로 내 계좌 id 해석."""
    import re
    digits = re.sub(r"[^0-9]", "", account_no or "")
    data = _get("/api/accounts", token)
    for a in data.get("accounts", []):
        if re.sub(r"[^0-9]", "", a["account_no"]) == digits:
            return a["id"]
    return None


def tool_get_accounts(args, ctx):
    return _get("/api/accounts", ctx.get("token"))


def tool_get_transactions(args, ctx):
    acc_id = _resolve_account_id(args.get("account_no", ""), ctx.get("token"))
    if acc_id is None:
        return {"error": "해당 계좌번호를 내 계좌에서 찾을 수 없습니다."}
    return _get(f"/api/accounts/{acc_id}/transactions", ctx.get("token"))


def _digits(account_no: str) -> str:
    """계좌번호에서 숫자만 남긴다(하이픈·공백 등 구분자 무관하게 조회하기 위함).
    백엔드(db.lookup_account)도 동일하게 정규화하지만, 에이전트가 LLM이 넘긴 문자열을
    그대로 신뢰하지 않고 한 번 더 정규화해 하이픈 포함 입력에서도 항상 같은 결과를 보장한다."""
    import re
    return re.sub(r"[^0-9]", "", account_no or "")


def tool_lookup_recipient(args, ctx):
    return _get("/api/accounts/lookup", ctx.get("token"), params={
        "account_no": _digits(args.get("account_no", "")),
        "from_account": args.get("from_account") or "",
    })


def tool_propose_transfer(args, ctx):
    """이체 제안만 생성(실행 안 함). 예금주·수수료 확인 후 pending 반환."""
    look = _get("/api/accounts/lookup", ctx.get("token"), params={
        "account_no": _digits(args.get("to_account", "")),
        "from_account": args.get("from_account") or "",
    })
    if "error" in look:
        return look
    proposal = {
        "from_account": args.get("from_account", ""),
        "to_account": look["account_no"],
        "bank_name": look["bank_name"],
        "holder_name": look["holder_name"],
        "amount": int(args.get("amount", 0)),
        "fee": look.get("fee", 0),
        "memo": args.get("memo") or None,
        "is_new_payee": look.get("is_new_payee", False),
    }
    # UI가 확인 카드를 띄우도록 컨텍스트에 저장
    ctx["proposal"] = proposal
    return {"status": "proposed", "proposal": proposal,
            "note": "사용자 확인 후 실행됩니다. 아직 이체되지 않았습니다."}


def execute_transfer(proposal: dict, token: str, password: str = "",
                     scheduled_at: float | None = None, delay_minutes: int = 0) -> dict:
    """사용자 확인 + 비밀번호 재인증 후 이체 실행(UI에서 호출).
    scheduled_at(예약 epoch) 또는 delay_minutes(지연이체) 지정 시 미래 실행."""
    return _post("/api/transfer", token, {
        "from_account": proposal["from_account"],
        "to_account": proposal["to_account"],
        "amount": proposal["amount"],
        "memo": proposal.get("memo"),
        "password": password,
        "scheduled_at": scheduled_at,
        "delay_minutes": delay_minutes,
    })


_PRODUCT_CATEGORIES = ("예금", "적금", "금리비교", "주택담보대출", "전세자금대출", "신용대출")
_LOAN_CATEGORIES = ("주택담보대출", "전세자금대출", "신용대출")


def tool_search_products(args, ctx):
    """상품안내 페이지와 동일한 FSS 상품 목록(/api/products)에서 검색·추천.
    반환된 products 목록 안의 상품만 안내해야 한다(목록 밖 상품 생성 금지)."""
    q = args.get("query", "") or ""
    cat = (args.get("category") or "").strip()
    # 카테고리 결정: 명시값 우선 → 질의어 추정 → 기본은 금리비교(예금+적금)
    if cat not in _PRODUCT_CATEGORIES:
        if "전세" in q:
            cat = "전세자금대출"
        elif "주택담보" in q:
            cat = "주택담보대출"
        elif "신용대출" in q:
            cat = "신용대출"
        elif "적금" in q:
            cat = "적금"
        elif "예금" in q:
            cat = "예금"
        elif "대출" in q:
            cat = "신용대출"  # 대출 종류를 특정할 수 없으면 가장 범용적인 신용대출로 추정
        else:
            cat = "금리비교"
    # /api/products는 캐시 미스 시 금융감독원(FSS) API를 실시간으로 순차 조회한다 — 실측 결과
    # 카테고리 하나(예: 적금)만도 콜드 상태에서 16초 넘게 걸렸고, "금리비교"는 예금+적금을
    # 순차로 두 번 호출해 그 두 배까지 걸릴 수 있다. 다른 도구들이 쓰는 공용 타임아웃(5초)으로는
    # 콜드 캐시일 때 항상 실패해서, 이 호출만 넉넉하게 늘림(캐시 적중 시엔 원래도 즉시 응답).
    data = _get("/api/products", params={"category": cat}, timeout=45)
    if "error" in data:
        return data
    products = data.get("products", [])[:12]  # 정렬 상위만(예금·적금은 최고금리, 대출은 최저금리 순)
    if not products:
        return {"category": cat, "products": [],
                "result": "해당 조건의 상품이 없습니다."}
    is_loan = cat in _LOAN_CATEGORIES
    items = []
    for p in products:
        options = p.get("options", [])
        if is_loan:
            rates = [o.get("min_rate") for o in options if o.get("min_rate") is not None]
            terms = []  # 대출 상품은 예금/적금과 달리 가입기간(개월) 개념이 없다
        else:
            rates = [o.get("max_rate") if o.get("max_rate") is not None else o.get("base_rate")
                     for o in options]
            rates = [r for r in rates if r is not None]
            terms = sorted({o.get("term_months") for o in options if o.get("term_months")})
        items.append({
            "bank": p.get("bank"),
            "name": p.get("product_name"),
            "category": p.get("category"),
            "best_rate": p.get("best_rate"),
            "min_rate": min(rates) if rates else None,
            "terms_months": terms,
            "url": p.get("url"),
        })
    return {
        "category": cat,
        "count": len(items),
        "products": items,
        "note": "위 products 는 상품안내 페이지와 동일한 FSS 데이터다. 반드시 이 목록 안의 상품만 "
                "추천·비교하고, 목록에 없는 상품명은 만들어내지 마라. 예금/적금은 best_rate가 최고금리, "
                "대출(주택담보대출/전세자금대출/신용대출)은 best_rate가 최저금리 기준이다.",
    }


def tool_get_faqs(args, ctx):
    return _get("/api/faqs", params={"q": args.get("q") or "", "limit": 5})


def tool_get_notices(args, ctx):
    return _get("/api/notices", params={"q": args.get("q") or "", "limit": 5})


def tool_get_documents(args, ctx):
    return _get("/api/documents", params={"q": args.get("q") or "", "limit": 5})


def tool_create_inquiry(args, ctx):
    if not ctx.get("token"):
        return {"error": "문의 접수는 로그인이 필요합니다."}
    return _post("/api/inquiries", ctx.get("token"), {
        "title": args.get("title", ""), "content": args.get("content", ""),
    })


# ── LangChain 도구 빌더 (요청별 token 바인딩) ─────────────────────────
def _build_lc_tools(ctx: dict):
    """기존 tool_*(args, ctx) 로직을 그대로 재사용해 LangChain StructuredTool 목록을 만든다.
    ctx(가변 dict)를 클로저로 캡처하므로 propose_transfer가 ctx['proposal']에 저장한 값을
    run_agent가 그래프 실행 후 읽을 수 있다."""
    from langchain_core.tools import StructuredTool

    def _dump(result: dict) -> str:
        return json.dumps(result, ensure_ascii=False)

    # 도메인별 도구(이름·설명은 기존 스키마 문구 유지)
    def get_accounts() -> str:
        return _dump(tool_get_accounts({}, ctx))

    def get_transactions(account_no: str) -> str:
        return _dump(tool_get_transactions({"account_no": account_no}, ctx))

    def lookup_recipient(account_no: str, from_account: str = "") -> str:
        return _dump(tool_lookup_recipient({"account_no": account_no, "from_account": from_account}, ctx))

    def propose_transfer(from_account: str, to_account: str, amount: int, memo: str = "") -> str:
        return _dump(tool_propose_transfer(
            {"from_account": from_account, "to_account": to_account, "amount": amount, "memo": memo}, ctx))

    def search_products(query: str, category: str = "") -> str:
        return _dump(tool_search_products({"query": query, "category": category}, ctx))

    def get_faqs(q: str = "") -> str:
        return _dump(tool_get_faqs({"q": q}, ctx))

    def get_notices(q: str = "") -> str:
        return _dump(tool_get_notices({"q": q}, ctx))

    def get_documents(q: str = "") -> str:
        return _dump(tool_get_documents({"q": q}, ctx))

    def create_inquiry(title: str, content: str) -> str:
        return _dump(tool_create_inquiry({"title": title, "content": content}, ctx))

    specs = [
        (get_accounts, "get_accounts", "로그인 사용자의 모든 계좌와 잔액을 조회한다."),
        (get_transactions, "get_transactions", "특정 계좌번호의 거래내역을 조회한다."),
        (lookup_recipient, "lookup_recipient", "받는 계좌의 예금주명·은행·예상 수수료를 조회한다."),
        (propose_transfer, "propose_transfer",
         "이체를 '제안'한다(실행하지 않음). 예금주·수수료를 확인해 반환하며, 실제 이체는 사용자가 확인 버튼을 눌러야 실행된다."),
        (search_products, "search_products",
         "상품안내 페이지와 동일한 FSS 상품 목록을 가져온다(예금/적금/주택담보대출/전세자금대출/신용대출). "
         "상품 추천·비교 시 반드시 이 도구를 호출하고, 반환된 products 목록 안의 상품만 안내한다."),
        (get_faqs, "get_faqs", "자주 묻는 질문(FAQ)을 검색한다."),
        (get_notices, "get_notices", "공지사항을 검색한다."),
        (get_documents, "get_documents", "서식·약관·설명서를 검색한다."),
        (create_inquiry, "create_inquiry", "1:1 문의를 접수한다(로그인 필요)."),
    ]
    return [StructuredTool.from_function(fn, name=name, description=desc) for fn, name, desc in specs]


# ── 에이전트 실행 (LangGraph create_react_agent) ──────────────────────
def run_agent(messages: list[dict], *, openai_key: str, model: str,
              token: str | None = None, system_prompt: str | None = None,
              max_steps: int = 5) -> dict:
    """LangGraph ReAct 에이전트로 도구호출을 오케스트레이션해 최종 결과를 반환한다.

    반환(계약 유지):
        {"kind": "message", "text": str}
        {"kind": "transfer_proposal", "proposal": {...}, "text": str}  — 이체 확인 대기
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent

    ctx: dict = {"token": token, "openai_key": openai_key}
    tools = _build_lc_tools(ctx)
    llm = ChatOpenAI(model=model, api_key=openai_key, temperature=0)
    graph = create_react_agent(llm, tools)

    sys = (system_prompt.strip() + "\n\n" + SYSTEM_PROMPT) if system_prompt else SYSTEM_PROMPT
    lc_messages = [SystemMessage(content=sys)]
    for m in messages:
        role, content = m.get("role"), m.get("content", "")
        lc_messages.append(HumanMessage(content=content) if role == "user" else AIMessage(content=content))

    out = graph.invoke({"messages": lc_messages}, {"recursion_limit": max(4, max_steps * 2)})
    final = out["messages"][-1].content or ""
    if isinstance(final, list):  # 멀티모달 대비: 텍스트 파트만 합침
        final = "".join(p.get("text", "") for p in final if isinstance(p, dict))

    # propose_transfer가 호출됐으면 이체 확인 대기로 반환(자동 실행 안 함)
    if ctx.get("proposal"):
        return {"kind": "transfer_proposal", "proposal": ctx["proposal"],
                "text": final or "이체 내용을 확인해 주세요."}
    return {"kind": "message", "text": final}
