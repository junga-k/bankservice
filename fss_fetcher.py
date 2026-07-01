"""금융감독원 금융상품한눈에 API → RAG 텍스트 변환 모듈.

공개 API:
    fetch_category(auth, category, fin_groups=None) -> list[tuple[str, str]]
    fetch_all(auth, fin_groups=None) -> dict[str, list[tuple[str, str]]]

FIN_GROUPS: 지원 금융권역 딕셔너리 (이름 → topFinGrpNo 코드)
PRODUCT_TYPES: 지원 카테고리 딕셔너리 (이름 → API 엔드포인트)
"""

from __future__ import annotations

import collections

import requests

BASE_URL = "http://finlife.fss.or.kr/finlifeapi/"

# 금융권역 코드 (topFinGrpNo)
FIN_GROUPS: dict[str, str] = {
    "은행":       "020000",
    "저축은행":   "030200",
    "신협":       "030300",
    "새마을금고": "030400",
    "우체국":     "030500",
}
_DEFAULT_GROUPS = ["020000"]  # 기본값: 은행만

# 금융상품 카테고리 → FSS API 엔드포인트
PRODUCT_TYPES: dict[str, str] = {
    "예금":        "depositProductsSearch.json",
    "적금":        "savingProductsSearch.json",
    "주택담보대출": "mortgageLoanProductsSearch.json",
    "전세자금대출": "rentHouseLoanProductsSearch.json",
    "신용대출":    "creditLoanProductsSearch.json",
}

# 기관명 → 카테고리별 상품 페이지 URL (없으면 해당 줄 생략)
_BANK_PRODUCT_URLS: dict[str, dict[str, str]] = {
    "KB국민은행": {
        "예금": "https://www.kbstar.com/quics?page=C025782",
        "적금": "https://www.kbstar.com/quics?page=C025782",
        "주택담보대출": "https://www.kbstar.com/quics?page=C025902",
        "전세자금대출": "https://www.kbstar.com/quics?page=C025902",
        "신용대출": "https://www.kbstar.com/quics?page=C025902",
    },
    "신한은행": {
        "예금": "https://www.shinhan.com/hpe/index.jsp#050201010000",
        "적금": "https://www.shinhan.com/hpe/index.jsp#050201010000",
        "주택담보대출": "https://www.shinhan.com/hpe/index.jsp#050301010000",
        "전세자금대출": "https://www.shinhan.com/hpe/index.jsp#050301010000",
        "신용대출": "https://www.shinhan.com/hpe/index.jsp#050301010000",
    },
    "우리은행": {
        "예금": "https://www.wooribank.com/wob/wobm/index.jsp",
        "적금": "https://www.wooribank.com/wob/wobm/index.jsp",
        "주택담보대출": "https://www.wooribank.com/wob/wobm/index.jsp",
        "전세자금대출": "https://www.wooribank.com/wob/wobm/index.jsp",
        "신용대출": "https://www.wooribank.com/wob/wobm/index.jsp",
    },
    "하나은행": {
        "예금": "https://www.kebhana.com/cont/mall/mall08/mall0801/index.jsp",
        "적금": "https://www.kebhana.com/cont/mall/mall08/mall0801/index.jsp",
        "주택담보대출": "https://www.kebhana.com/cont/mall/mall08/mall0802/index.jsp",
        "전세자금대출": "https://www.kebhana.com/cont/mall/mall08/mall0802/index.jsp",
        "신용대출": "https://www.kebhana.com/cont/mall/mall08/mall0802/index.jsp",
    },
    "NH농협은행": {
        "예금": "https://banking.nonghyup.com/nhbank.html",
        "적금": "https://banking.nonghyup.com/nhbank.html",
        "주택담보대출": "https://banking.nonghyup.com/nhbank.html",
        "전세자금대출": "https://banking.nonghyup.com/nhbank.html",
        "신용대출": "https://banking.nonghyup.com/nhbank.html",
    },
    "IBK기업은행": {
        "예금": "https://www.ibk.co.kr/index.jsp",
        "적금": "https://www.ibk.co.kr/index.jsp",
        "주택담보대출": "https://www.ibk.co.kr/index.jsp",
        "전세자금대출": "https://www.ibk.co.kr/index.jsp",
        "신용대출": "https://www.ibk.co.kr/index.jsp",
    },
    "카카오뱅크": {
        "예금": "https://www.kakaobank.com/products/depositAccount",
        "적금": "https://www.kakaobank.com/products/savingsAccount",
        "신용대출": "https://www.kakaobank.com/products/creditLoan",
    },
    "케이뱅크": {
        "예금": "https://www.kbanknow.com/ib20/mnu/FPMMPD0001M",
        "적금": "https://www.kbanknow.com/ib20/mnu/FPMMPD0001M",
        "신용대출": "https://www.kbanknow.com/ib20/mnu/FPMMLC0001M",
    },
    "토스뱅크": {
        "예금": "https://www.tossbank.com/products/deposit",
        "적금": "https://www.tossbank.com/products/saving",
        "신용대출": "https://www.tossbank.com/products/loan",
    },
    "SC제일은행": {
        "예금": "https://www.standardchartered.co.kr/np/kr/pl/de/deMain.jsp",
        "적금": "https://www.standardchartered.co.kr/np/kr/pl/de/deMain.jsp",
        "신용대출": "https://www.standardchartered.co.kr/np/kr/pl/lo/loMain.jsp",
    },
    "한국씨티은행": {
        "예금": "https://www.citibank.co.kr/index.jsp",
        "적금": "https://www.citibank.co.kr/index.jsp",
        "신용대출": "https://www.citibank.co.kr/index.jsp",
    },
    "Sh수협은행": {
        "예금": "https://www.suhyup-bank.com",
        "적금": "https://www.suhyup-bank.com",
        "신용대출": "https://www.suhyup-bank.com",
    },
    "광주은행": {
        "예금": "https://www.kjbank.com",
        "적금": "https://www.kjbank.com",
        "주택담보대출": "https://www.kjbank.com",
        "신용대출": "https://www.kjbank.com",
    },
    "경남은행": {
        "예금": "https://www.knbank.co.kr",
        "적금": "https://www.knbank.co.kr",
        "주택담보대출": "https://www.knbank.co.kr",
        "신용대출": "https://www.knbank.co.kr",
    },
    "아이엠뱅크": {
        "예금": "https://www.imbank.co.kr",
        "적금": "https://www.imbank.co.kr",
        "주택담보대출": "https://www.imbank.co.kr",
        "신용대출": "https://www.imbank.co.kr",
    },
    "부산은행": {
        "예금": "https://www.busanbank.co.kr",
        "적금": "https://www.busanbank.co.kr",
        "주택담보대출": "https://www.busanbank.co.kr",
        "신용대출": "https://www.busanbank.co.kr",
    },
    "전북은행": {
        "예금": "https://www.jbbank.co.kr",
        "적금": "https://www.jbbank.co.kr",
        "신용대출": "https://www.jbbank.co.kr",
    },
    "제주은행": {
        "예금": "https://www.jejubank.co.kr",
        "적금": "https://www.jejubank.co.kr",
        "신용대출": "https://www.jejubank.co.kr",
    },
    "한국스탠다드차타드은행": {
        "예금": "https://www.standardchartered.co.kr/np/kr/pl/de/deMain.jsp",
        "적금": "https://www.standardchartered.co.kr/np/kr/pl/de/deMain.jsp",
        "신용대출": "https://www.standardchartered.co.kr/np/kr/pl/lo/loMain.jsp",
    },
    "우체국": {
        "예금": "https://www.postoffice.go.kr/",
        "적금": "https://www.postoffice.go.kr/",
    },
}


def _fetch_pages(
    auth: str, endpoint: str, top_fin_grp_no: str = "020000"
) -> tuple[list[dict], list[dict]]:
    base_all: list[dict] = []
    opt_all: list[dict] = []
    page = 1
    while True:
        resp = requests.get(
            BASE_URL + endpoint,
            params={"auth": auth, "topFinGrpNo": top_fin_grp_no, "pageNo": page},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json().get("result", {})
        if result.get("err_cd", "000") != "000":
            raise ValueError(f"FSS API 오류: {result.get('err_msg', '알 수 없는 오류')}")
        base_all.extend(result.get("baseList") or [])
        opt_all.extend(result.get("optionList") or [])
        max_page = int(result.get("max_page_no") or 1)
        if page >= max_page:
            break
        page += 1
    return base_all, opt_all


def _group_options(option_list: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for opt in option_list:
        code = opt.get("fin_prdt_cd", "")
        if code:
            grouped[code].append(opt)
    return dict(grouped)


def _format_product(base: dict, options: list[dict], category: str) -> str:
    lines = [f"[{base.get('kor_co_nm', '')}] {base.get('fin_prdt_nm', '')}"]
    lines.append(f"유형: {category}")

    field_labels = [
        ("join_way",    "가입방법"),
        ("join_member", "가입대상"),
        ("spcl_cnd",    "특별우대조건"),
        ("etc_note",    "기타사항"),
    ]
    for field, label in field_labels:
        value = (base.get(field) or "").strip()
        if value:
            lines.append(f"{label}: {value}")

    limit = base.get("max_limit")
    if limit:
        limit_label = "대출한도" if "대출" in category else "최고한도"
        lines.append(f"{limit_label}: {limit}원")

    if options:
        lines.append("금리 옵션:")
        sorted_opts = sorted(options, key=lambda o: int(o.get("save_trm") or 0))
        for opt in sorted_opts:
            trm = opt.get("save_trm", "")
            rate = opt.get("intr_rate")
            rate2 = opt.get("intr_rate2")
            rate_type = opt.get("intr_rate_type_nm", "")
            rate_str = f"기본 {rate}%" if rate is not None else "기본 -"
            rate2_str = f"최고 {rate2}%" if rate2 is not None else "최고 -"
            lines.append(f"- {trm}개월: {rate_str} / {rate2_str} ({rate_type})")

    bank = base.get("kor_co_nm", "")
    url = _BANK_PRODUCT_URLS.get(bank, {}).get(category)
    if url:
        lines.append(f"상품 페이지: {url}")

    return "\n".join(lines)


def fetch_category(
    auth: str,
    category: str,
    fin_groups: list[str] | None = None,
) -> list[tuple[str, str]]:
    """지정한 카테고리의 금융 상품을 수집해 (doc_name, text) 목록으로 반환한다."""
    if fin_groups is None:
        fin_groups = _DEFAULT_GROUPS
    endpoint = PRODUCT_TYPES[category]
    base_all: list[dict] = []
    opt_all: list[dict] = []
    for grp in fin_groups:
        try:
            b, o = _fetch_pages(auth, endpoint, grp)
            base_all.extend(b)
            opt_all.extend(o)
        except Exception:
            pass  # 특정 권역이 해당 카테고리를 미지원하면 건너뜀
    options_by_code = _group_options(opt_all)
    result = []
    for base in base_all:
        code = base.get("fin_prdt_cd", "")
        company = base.get("kor_co_nm", "")
        name = base.get("fin_prdt_nm", "")
        doc_name = f"FSS_{category}_{company}_{name}"
        text = _format_product(base, options_by_code.get(code, []), category)
        result.append((doc_name, text))
    return result


def fetch_all(
    auth: str,
    fin_groups: list[str] | None = None,
) -> dict[str, list[tuple[str, str]]]:
    """5개 카테고리 전체 수집. {카테고리: [(doc_name, text), ...]} 반환."""
    return {cat: fetch_category(auth, cat, fin_groups) for cat in PRODUCT_TYPES}
