"""fss_fetcher 모듈 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import fss_fetcher
from unittest.mock import patch, MagicMock


def test_product_types_has_five_categories():
    """PRODUCT_TYPES에 5개 카테고리가 존재한다."""
    expected = {"예금", "적금", "주택담보대출", "전세자금대출", "신용대출"}
    assert set(fss_fetcher.PRODUCT_TYPES.keys()) == expected


def test_fin_groups_has_five_groups():
    """FIN_GROUPS에 5개 금융권역이 존재한다."""
    expected = {"은행", "저축은행", "신협", "새마을금고", "우체국"}
    assert set(fss_fetcher.FIN_GROUPS.keys()) == expected


def test_fin_groups_bank_code():
    """은행 권역 코드는 020000이다."""
    assert fss_fetcher.FIN_GROUPS["은행"] == "020000"


def test_format_product_contains_bank_name():
    """_format_product()는 은행명과 상품명을 포함한 텍스트를 반환한다."""
    base = {
        "kor_co_nm": "KB국민은행",
        "fin_prdt_nm": "KB스타 정기예금",
        "join_way": "인터넷,스마트폰",
        "join_member": "실명의 개인",
        "spcl_cnd": "",
        "etc_note": "",
        "max_limit": None,
    }
    options = [
        {"save_trm": "12", "intr_rate": 3.5, "intr_rate2": 4.0,
         "intr_rate_type_nm": "단리"},
    ]
    text = fss_fetcher._format_product(base, options, "예금")
    assert "KB국민은행" in text
    assert "KB스타 정기예금" in text
    assert "3.5%" in text
    assert "4.0%" in text


def test_fetch_category_uses_default_group():
    """fin_groups 미지정 시 기본 은행 권역(020000)으로 요청한다."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "result": {
            "err_cd": "000",
            "baseList": [],
            "optionList": [],
            "max_page_no": 1,
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch("fss_fetcher.requests.get", return_value=mock_response) as mock_get:
        fss_fetcher.fetch_category("fake-auth", "예금")
        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["params"]["topFinGrpNo"] == "020000"


def test_fetch_category_multi_group():
    """fin_groups에 여러 권역 지정 시 각 권역마다 API를 호출한다."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "result": {
            "err_cd": "000",
            "baseList": [],
            "optionList": [],
            "max_page_no": 1,
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch("fss_fetcher.requests.get", return_value=mock_response) as mock_get:
        fss_fetcher.fetch_category("fake-auth", "예금",
                                   fin_groups=["020000", "030200"])
        assert mock_get.call_count == 2
