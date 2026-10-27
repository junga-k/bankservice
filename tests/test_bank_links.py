"""AI은행원 상품 추천 답변의 링크 정리.

제목의 은행명에 링크가 붙고 정작 "상품 링크" 문구에는 링크가 없던 것을, 은행명 링크는
없애고 상품마다 "홈페이지에서 검색하기 ↗"를 그 상품 은행의 사이트로 잇도록 바꿨다.
"""
from bank_links import BANK_URLS, inject_bank_links

GUIDE = "추천해 드린 상품은 아래 은행 사이트로 이동하신 후 상품명을 검색하시면 확인하실 수 있습니다."

ANSWER = """다음은 12개월 만기 적금 상품 중 금리가 높은 상위 5개 상품입니다.

### 1. 케이뱅크 - 마이키즈 적금
- **금리**: 최고 7.3%
- [상품 링크](https://example.com/kbank)

### 2. 경남은행 - 오면우대! 하면우대! 정기적금
- **금리**: 최고 7%
- 상품 링크"""


def test_상품마다_해당_은행_사이트로_검색_링크():
    out = inject_bank_links(ANSWER)
    assert f"- [홈페이지에서 검색하기 ↗]({BANK_URLS['케이뱅크']})" in out
    assert f"- [홈페이지에서 검색하기 ↗]({BANK_URLS['경남은행']})" in out
    assert "상품 링크" not in out
    assert "example.com" not in out   # LLM 이 단 URL 은 쓰지 않는다


def test_제목의_은행명에는_링크가_없다():
    out = inject_bank_links(ANSWER)
    assert "### 1. 케이뱅크 - 마이키즈 적금" in out
    assert "케이뱅크 ↗" not in out


def test_안내문구는_첫_상품_앞에_한_번만():
    out = inject_bank_links(ANSWER)
    assert out.count(GUIDE) == 1
    assert out.index(GUIDE) < out.index("### 1. 케이뱅크")
    assert inject_bank_links(out).count(GUIDE) == 1   # 이미 있으면 또 넣지 않는다


def test_상품_링크가_없는_답변은_그대로():
    text = "하나은행 218-910111-12345 이영희님에게 50,000원 이체를 준비했어요."
    assert inject_bank_links(text) == text


def test_은행을_못_찾으면_링크_없이_문구만():
    out = inject_bank_links("1. 좋은 적금\n- 상품 링크")
    assert out.endswith("- 홈페이지에서 검색하기")
    assert GUIDE not in out
