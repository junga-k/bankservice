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

import requests

BACKEND_URL = "http://localhost:8000"
_TIMEOUT = 5

SYSTEM_PROMPT = (
    "당신은 매치뱅크의 은행업무 AI 상담원입니다. 사용자의 계좌 조회, 거래내역, 이체, "
    "금융상품 추천·비교, 고객지원(공지/FAQ/문의) 요청을 도구를 사용해 처리합니다.\n"
    "- 도구가 필요한 요청은 반드시 도구를 호출해 실제 데이터를 근거로 답하세요. 추측하지 마세요.\n"
    "- 이체는 propose_transfer로 '제안'만 하세요. 실제 실행은 사용자가 화면에서 확인합니다.\n"
    "- 상품 추천·비교 요청에는 반드시 search_products를 호출하고, 그 결과 products 목록(상품안내 "
    "페이지와 동일한 FSS 데이터) 안의 상품만 안내하세요. 목록에 없는 상품명·수치를 지어내지 마세요.\n"
    "- 금융상품 정보는 참고용이며 투자·금융 자문이 아님을 필요 시 안내하세요.\n"
    "- 답변은 한국어로, 금액은 원 단위로 읽기 쉽게 표기하세요."
)


# ── 백엔드 호출 헬퍼 ──────────────────────────────────────────────────
def _auth_headers(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _get(path: str, token: str | None = None, params: dict | None = None) -> dict:
    r = requests.get(f"{BACKEND_URL}{path}", headers=_auth_headers(token),
                     params=params, timeout=_TIMEOUT)
    if r.status_code == 401:
        return {"error": "로그인이 필요합니다."}
    if not r.ok:
        return {"error": _detail(r)}
    return r.json()


def _post(path: str, token: str | None, body: dict) -> dict:
    r = requests.post(f"{BACKEND_URL}{path}", headers=_auth_headers(token),
                      json=body, timeout=_TIMEOUT)
    if r.status_code == 401:
        return {"error": "로그인이 필요합니다."}
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


def tool_lookup_recipient(args, ctx):
    return _get("/api/accounts/lookup", ctx.get("token"), params={
        "account_no": args.get("account_no", ""),
        "from_account": args.get("from_account") or "",
    })


def tool_propose_transfer(args, ctx):
    """이체 제안만 생성(실행 안 함). 예금주·수수료 확인 후 pending 반환."""
    look = _get("/api/accounts/lookup", ctx.get("token"), params={
        "account_no": args.get("to_account", ""),
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
    }
    # UI가 확인 카드를 띄우도록 컨텍스트에 저장
    ctx["proposal"] = proposal
    return {"status": "proposed", "proposal": proposal,
            "note": "사용자 확인 후 실행됩니다. 아직 이체되지 않았습니다."}


def execute_transfer(proposal: dict, token: str) -> dict:
    """사용자 확인 후 실제 이체 실행(UI에서 호출)."""
    return _post("/api/transfer", token, {
        "from_account": proposal["from_account"],
        "to_account": proposal["to_account"],
        "amount": proposal["amount"],
        "memo": proposal.get("memo"),
    })


def tool_search_products(args, ctx):
    """상품안내 페이지와 동일한 FSS 상품 목록(/api/products)에서 검색·추천.
    반환된 products 목록 안의 상품만 안내해야 한다(목록 밖 상품 생성 금지)."""
    q = args.get("query", "") or ""
    cat = (args.get("category") or "").strip()
    # 카테고리 결정: 명시값 우선 → 질의어 추정 → 기본은 금리비교(예금+적금)
    if cat not in ("예금", "적금", "금리비교"):
        if "적금" in q:
            cat = "적금"
        elif "예금" in q:
            cat = "예금"
        else:
            cat = "금리비교"
    data = _get("/api/products", params={"category": cat})
    if "error" in data:
        return data
    products = data.get("products", [])[:12]  # best_rate 내림차순 정렬됨, 상위만
    if not products:
        return {"category": cat, "products": [],
                "result": "해당 조건의 상품이 없습니다. (예금·적금만 안내 가능)"}
    items = []
    for p in products:
        rates = [o.get("max_rate") if o.get("max_rate") is not None else o.get("base_rate")
                 for o in p.get("options", [])]
        rates = [r for r in rates if r is not None]
        terms = sorted({o.get("term_months") for o in p.get("options", []) if o.get("term_months")})
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
                "추천·비교하고, 목록에 없는 상품명은 만들어내지 마라. 대출 상품은 현재 목록에 없다.",
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


# ── 도구 스키마(도메인별 그룹) ────────────────────────────────────────
def _fn(name, desc, props, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required or []},
    }}

ACCOUNT_TOOLS = [
    _fn("get_accounts", "로그인 사용자의 모든 계좌와 잔액을 조회한다.", {}),
    _fn("get_transactions", "특정 계좌번호의 거래내역을 조회한다.",
        {"account_no": {"type": "string", "description": "조회할 계좌번호"}}, ["account_no"]),
]
TRANSFER_TOOLS = [
    _fn("lookup_recipient", "받는 계좌의 예금주명·은행·예상 수수료를 조회한다.",
        {"account_no": {"type": "string"}, "from_account": {"type": "string", "description": "출금 계좌번호(선택)"}},
        ["account_no"]),
    _fn("propose_transfer",
        "이체를 '제안'한다(실행하지 않음). 예금주·수수료를 확인해 반환하며, 실제 이체는 사용자가 확인 버튼을 눌러야 실행된다.",
        {"from_account": {"type": "string", "description": "출금 계좌번호"},
         "to_account": {"type": "string", "description": "받는 계좌번호"},
         "amount": {"type": "integer", "description": "이체 금액(원)"},
         "memo": {"type": "string", "description": "받는 분 통장 표시(선택)"}},
        ["from_account", "to_account", "amount"]),
]
PRODUCT_TOOLS = [
    _fn("search_products",
        "상품안내 페이지와 동일한 FSS 예금·적금 상품 목록을 가져온다. 상품 추천·비교 시 반드시 이 도구를 호출하고, 반환된 products 목록 안의 상품만 안내한다.",
        {"query": {"type": "string", "description": "검색어(예: 금리 높은 정기예금)"},
         "category": {"type": "string", "description": "예금 / 적금 / 금리비교(예금+적금) 중 하나(선택)"}},
        ["query"]),
]
SUPPORT_TOOLS = [
    _fn("get_faqs", "자주 묻는 질문(FAQ)을 검색한다.", {"q": {"type": "string"}}),
    _fn("get_notices", "공지사항을 검색한다.", {"q": {"type": "string"}}),
    _fn("get_documents", "서식·약관·설명서를 검색한다.", {"q": {"type": "string"}}),
    _fn("create_inquiry", "1:1 문의를 접수한다(로그인 필요).",
        {"title": {"type": "string"}, "content": {"type": "string"}}, ["title", "content"]),
]

ALL_TOOLS = ACCOUNT_TOOLS + TRANSFER_TOOLS + PRODUCT_TOOLS + SUPPORT_TOOLS

_DISPATCH = {
    "get_accounts": tool_get_accounts,
    "get_transactions": tool_get_transactions,
    "lookup_recipient": tool_lookup_recipient,
    "propose_transfer": tool_propose_transfer,
    "search_products": tool_search_products,
    "get_faqs": tool_get_faqs,
    "get_notices": tool_get_notices,
    "get_documents": tool_get_documents,
    "create_inquiry": tool_create_inquiry,
}


# ── 에이전트 실행 루프 ────────────────────────────────────────────────
def run_agent(messages: list[dict], *, openai_key: str, model: str,
              token: str | None = None, system_prompt: str | None = None,
              max_steps: int = 5) -> dict:
    """도구호출 루프를 돌려 최종 결과를 반환한다.

    반환:
        {"kind": "message", "text": str}                     — 일반 답변
        {"kind": "transfer_proposal", "proposal": {...}, "text": str}  — 이체 확인 대기
    """
    from openai import OpenAI

    client = OpenAI(api_key=openai_key)
    ctx: dict = {"token": token, "openai_key": openai_key}

    sys = (system_prompt.strip() + "\n\n" + SYSTEM_PROMPT) if system_prompt else SYSTEM_PROMPT
    oai_messages = [{"role": "system", "content": sys}]
    oai_messages += [{"role": m["role"], "content": m["content"]} for m in messages]

    for _ in range(max_steps):
        resp = client.chat.completions.create(
            model=model, messages=oai_messages, tools=ALL_TOOLS, tool_choice="auto",
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            return {"kind": "message", "text": msg.content or ""}

        # 어시스턴트의 tool_calls 메시지를 대화에 추가
        oai_messages.append({
            "role": "assistant", "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ],
        })

        proposal_made = False
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            fn = _DISPATCH.get(name)
            result = fn(args, ctx) if fn else {"error": f"알 수 없는 도구: {name}"}
            if name == "propose_transfer" and "proposal" in ctx:
                proposal_made = True
            oai_messages.append({
                "role": "tool", "tool_call_id": tc.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

        # 이체 제안이 나오면 확인을 위해 루프 종료
        if proposal_made:
            follow = client.chat.completions.create(model=model, messages=oai_messages)
            return {"kind": "transfer_proposal", "proposal": ctx["proposal"],
                    "text": follow.choices[0].message.content or "이체 내용을 확인해 주세요."}

    # 스텝 초과 시 마지막으로 도구 없이 한 번 더 정리
    resp = client.chat.completions.create(model=model, messages=oai_messages)
    return {"kind": "message", "text": resp.choices[0].message.content or ""}
