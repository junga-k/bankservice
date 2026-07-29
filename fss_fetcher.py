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


def _group_options(option_list: list[dict]) -> dict[tuple[str, str], list[dict]]:
    """fin_prdt_cd만으로 묶으면 은행마다 독립적으로 매기는 상품코드가 우연히 겹칠 때
    서로 다른 은행의 옵션이 섞인다. fin_co_no까지 포함해 묶는다."""
    grouped: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for opt in option_list:
        code = opt.get("fin_prdt_cd", "")
        if code:
            grouped[(opt.get("fin_co_no", ""), code)].append(opt)
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
        code = (base.get("fin_co_no", ""), base.get("fin_prdt_cd", ""))
        company = base.get("kor_co_nm", "")
        name = base.get("fin_prdt_nm", "")
        doc_name = f"FSS_{category}_{company}_{name}"
        text = _format_product(base, options_by_code.get(code, []), category)
        result.append((doc_name, text))
    return result


_BANK_NAME_ALIASES = {
    # FSS API의 법정 등록명과 site/js/main.js BANK_BRAND의 통용 브랜드명이 달라
    # 배지·URL 매핑이 깨지는 은행들을 통용명으로 정규화한다.
    # (아이엠뱅크는 여기 넣지 않는다 — _BANK_PRODUCT_URLS에 "아이엠뱅크" 키만 있고
    #  "대구은행" 키는 없어서, 여기서 리네임하면 URL 매핑이 깨진다.
    #  대신 site/js/main.js의 BANK_BRAND에 "아이엠뱅크" 키를 별도로 추가해 처리한다.)
    "한국스탠다드차타드은행": "SC제일은행",
    "중소기업은행": "IBK기업은행",
}


def _clean_bank_name(name: str) -> str:
    """'주식회사 케이뱅크' 같은 법인격 접두어를 제거하고, 법정 등록명을 통용 브랜드명으로 정규화해 배지·URL 매핑과 맞춘다."""
    name = name.replace("주식회사", "").strip()
    return _BANK_NAME_ALIASES.get(name, name)


_JOIN_DENY_LABELS = {"1": "가입제한 없음", "2": "서민전용", "3": "일부제한"}


def _format_dcls_day(raw: str | None) -> str | None:
    """FSS의 'YYYYMMDD' 공시일 문자열을 'YYYY.MM.DD'로 변환한다."""
    if not raw or len(raw) != 8:
        return None
    return f"{raw[:4]}.{raw[4:6]}.{raw[6:8]}"


LOAN_CATEGORIES = {"주택담보대출", "전세자금대출", "신용대출"}
_CREDIT_GRADE_FIELDS = (
    "crdt_grad_1", "crdt_grad_4", "crdt_grad_5", "crdt_grad_6",
    "crdt_grad_10", "crdt_grad_11", "crdt_grad_12", "crdt_grad_13",
)


def fetch_category_structured(
    auth: str,
    category: str,
    fin_groups: list[str] | None = None,
) -> list[dict]:
    """지정한 카테고리의 금융 상품을 구조화된 dict 목록으로 반환한다 (사이트 상품안내용)."""
    if category in LOAN_CATEGORIES:
        return _fetch_loan_structured(auth, category, fin_groups)

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
        code = (base.get("fin_co_no", ""), base.get("fin_prdt_cd", ""))
        bank = _clean_bank_name(base.get("kor_co_nm", ""))
        name = base.get("fin_prdt_nm", "")
        sorted_opts = sorted(options_by_code.get(code, []), key=lambda o: int(o.get("save_trm") or 0))
        options = [
            {
                "term_months": int(o.get("save_trm") or 0),
                "base_rate": o.get("intr_rate"),
                "max_rate": o.get("intr_rate2"),
                "rate_type": o.get("intr_rate_type_nm", ""),
                "save_type": o.get("rsrv_type_nm"),  # 적금만 존재 (정액적립식/자유적립식)
            }
            for o in sorted_opts
        ]
        rates = [o["max_rate"] if o["max_rate"] is not None else o["base_rate"] for o in options]
        rates = [r for r in rates if r is not None]
        result.append({
            "bank": bank,
            "product_name": name,
            "category": category,
            "join_way": (base.get("join_way") or "").strip(),
            "join_member": (base.get("join_member") or "").strip(),
            "spcl_cnd": (base.get("spcl_cnd") or "").strip(),
            "etc_note": (base.get("etc_note") or "").strip(),
            "mtrt_int": (base.get("mtrt_int") or "").strip(),
            "join_deny_label": _JOIN_DENY_LABELS.get(str(base.get("join_deny") or ""), ""),
            "dcls_date": _format_dcls_day(base.get("dcls_strt_day")),
            "options": options,
            "best_rate": max(rates) if rates else None,
            "url": _BANK_PRODUCT_URLS.get(bank, {}).get(category),
        })
    return result


def _fetch_loan_structured(
    auth: str,
    category: str,
    fin_groups: list[str] | None = None,
) -> list[dict]:
    """대출 3종(주택담보대출/전세자금대출/신용대출) 전용 파싱.
    예금/적금과 달리 optionList 스키마가 다르다:
    - 주택담보대출/전세자금대출: rpay_type_nm(상환방식)·lend_rate_type_nm(금리유형)·lend_rate_min/max/avg
    - 신용대출: crdt_lend_rate_type_nm(금리유형) + crdt_grad_1~13(신용점수구간별 금리)·crdt_grad_avg
    best_rate는 대출상품 특성상 '가장 낮은 금리(최저 연 X%)'가 기준이다(예금과 반대)."""
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
            pass
    options_by_code = _group_options(opt_all)
    is_credit = category == "신용대출"
    result = []
    for base in base_all:
        code = (base.get("fin_co_no", ""), base.get("fin_prdt_cd", ""))
        bank = _clean_bank_name(base.get("kor_co_nm", ""))
        name = base.get("fin_prdt_nm", "")
        opts = options_by_code.get(code, [])
        if is_credit:
            options = []
            for o in opts:
                # 기준금리·가산금리·가감조정금리는 대출금리를 구성하는 요소일 뿐 실제 적용금리가
                # 아니므로 제외한다(섞으면 가감조정금리 같은 작은 값이 최저금리로 잘못 집계된다).
                if o.get("crdt_lend_rate_type_nm") != "대출금리":
                    continue
                grades = [o.get(f) for f in _CREDIT_GRADE_FIELDS]
                grades = [g for g in grades if g is not None]
                avg = o.get("crdt_grad_avg")
                options.append({
                    "rate_type": "대출금리",
                    "min_rate": min(grades) if grades else avg,
                    "max_rate": max(grades) if grades else avg,
                    "avg_rate": avg,
                })
        else:
            options = [
                {
                    "rate_type": " · ".join(
                        v for v in (o.get("rpay_type_nm"), o.get("lend_rate_type_nm")) if v
                    ),
                    "min_rate": o.get("lend_rate_min"),
                    "max_rate": o.get("lend_rate_max"),
                    "avg_rate": o.get("lend_rate_avg"),
                }
                for o in opts
            ]
        min_rates = [o["min_rate"] for o in options if o["min_rate"] is not None]
        result.append({
            "bank": bank,
            "product_name": name,
            "category": category,
            "join_way": (base.get("join_way") or "").strip(),
            "join_member": "",
            "spcl_cnd": "",
            "etc_note": "",
            "mtrt_int": "",
            "join_deny_label": "",
            "dcls_date": _format_dcls_day(base.get("dcls_strt_day")),
            "options": options,
            "best_rate": min(min_rates) if min_rates else None,
            "url": _BANK_PRODUCT_URLS.get(bank, {}).get(category),
            "loan_limit": (base.get("loan_lmt") or "").strip(),
            "early_repay_fee": (base.get("erly_rpay_fee") or "").strip(),
        })
    return result


def fetch_all(
    auth: str,
    fin_groups: list[str] | None = None,
) -> dict[str, list[tuple[str, str]]]:
    """5개 카테고리 전체 수집. {카테고리: [(doc_name, text), ...]} 반환."""
    return {cat: fetch_category(auth, cat, fin_groups) for cat in PRODUCT_TYPES}
