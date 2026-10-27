"""AI은행원 답변의 은행·상품 링크 정리(화면에 그리기 직전 후처리).

app.py 는 import 하면 Streamlit 화면이 실행돼 테스트에서 부를 수 없어서 따로 둔다.
"""
from __future__ import annotations

import re

# ── 은행 홈페이지 URL 매핑 ────────────────────────────────────────────
BANK_URLS: dict[str, str] = {
    "KB국민은행":  "https://obank.kbstar.com/quics?page=C016528",
    "국민은행":    "https://obank.kbstar.com/quics?page=C016528",
    "우리은행":    "https://spot.wooribank.com/pot/Dream?withyou=PODEP0001",
    "NH농협은행":  "https://wmall.nonghyup.com/servlet/SFBCW0000R.view",
    "농협은행":    "https://wmall.nonghyup.com/servlet/SFBCW0000R.view",
    "토스뱅크":   "https://www.tossbank.com/",
    "제주은행":    "https://www.jejubank.co.kr/hmpg/prdGdnc/sid.do",
    "SC제일은행":  "https://www.standardchartered.co.kr/np/kr/pl/se/SavingList.jsp",
    "신한은행":    "https://www.shinhan.com",
    "하나은행":    "https://www.kebhana.com",
    "카카오뱅크":  "https://www.kakaobank.com",
    "IBK기업은행": "https://www.ibk.co.kr",
    "기업은행":    "https://www.ibk.co.kr",
    "케이뱅크":   "https://www.kbanknow.com",
    "부산은행":    "https://www.busanbank.co.kr",
    "대구은행":    "https://www.dgb.co.kr",
    "전북은행":    "https://www.jbbank.co.kr",
    "경남은행":    "https://www.knbank.co.kr",
    "광주은행":    "https://www.kjbank.com",
}


_PRODUCT_SEARCH_LABEL = "홈페이지에서 검색하기"
_PRODUCT_SEARCH_GUIDE = ("추천해 드린 상품은 아래 은행 사이트로 이동하신 후 상품명을 검색하시면 "
                         "확인하실 수 있습니다.")
# "상품 링크"·"상품 페이지"·"홈페이지에서 검색하기"만 있는 글머리 줄(링크를 풀고 난 뒤 기준)
_PRODUCT_LINK_LINE = re.compile(
    r'^(?P<lead>[ \t]*(?:[-*○◦•][ \t]*)?)(?:\*\*)?(?:상품\s*링크|상품\s*페이지|홈페이지에서\s*검색하기)'
    r'(?:\*\*)?[ \t]*:?[ \t]*$')
_BANKS_LONGEST_FIRST = sorted(BANK_URLS, key=len, reverse=True)


def inject_bank_links(text: str) -> str:
    """어시스턴트 응답의 링크를 정리한다.

    - 기존 마크다운 링크(상품명 등)는 글자만 남긴다. LLM 이 단 상품 URL 은 은행 사이트
      구조가 바뀌면 깨지기 쉬워서 쓰지 않는다.
    - 상품마다 붙는 "상품 링크" 줄을 "홈페이지에서 검색하기 ↗"로 바꿔, 그 줄 위에서
      가장 가까이 나온 은행명의 사이트로 잇는다(보통 그 상품의 제목 줄).
    - 그런 링크가 하나라도 있으면 첫 상품 앞에 "은행 사이트에서 상품명을 검색하라"는
      안내를 한 번 넣는다. 프롬프트에만 맡기면 빠질 수 있어서 화면에서 보장한다.
    은행명 자체에 링크를 다는 것은 2026-10-27 에 없앴다(제목이 링크로 뒤덮여서).
    """
    result = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)

    lines = result.split("\n")
    current_bank = None
    linked = False
    for i, line in enumerate(lines):
        m = _PRODUCT_LINK_LINE.match(line)
        if m:
            url = BANK_URLS.get(current_bank) if current_bank else None
            if url:
                lines[i] = f"{m.group('lead')}[{_PRODUCT_SEARCH_LABEL} ↗]({url})"
                linked = True
            else:
                lines[i] = f"{m.group('lead')}{_PRODUCT_SEARCH_LABEL}"
            continue
        # 한 줄에 은행명이 여럿이면 가장 앞에 나온 것을 그 상품의 은행으로 본다
        hits = [(line.find(b), b) for b in _BANKS_LONGEST_FIRST if b in line]
        if hits:
            current_bank = min(hits)[1]

    if linked and _PRODUCT_SEARCH_GUIDE not in result:
        first_item = next((i for i, l in enumerate(lines)
                           if re.match(r'^[ \t]*(?:#+[ \t]*)?\d+\.[ \t]', l)), None)
        if first_item is None:
            lines += ["", _PRODUCT_SEARCH_GUIDE]
        else:
            lines[first_item:first_item] = [_PRODUCT_SEARCH_GUIDE, ""]
    return "\n".join(lines)
