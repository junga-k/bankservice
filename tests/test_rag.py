"""rag 모듈 단위 테스트."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from unittest.mock import MagicMock, patch


def _mock_es(count=0, hits=None):
    es = MagicMock()
    es.indices.exists.return_value = True
    es.count.return_value = {"count": count}
    es.search.return_value = {"hits": {"hits": hits or []}}
    return es


def test_search_empty_index_returns_empty():
    """인덱스가 비어 있으면 빈 문자열을 반환한다."""
    with patch("rag._get_es", return_value=_mock_es(count=0)):
        import rag
        result = rag.search("예금 금리", "fake-key")
    assert result == ""


def test_search_returns_formatted_chunks():
    """검색 결과가 있으면 포맷된 문자열을 반환한다."""
    hits = [{"_source": {"doc_name": "FSS_예금_KB국민은행_테스트", "text": "예금 금리 3.5%"}}]
    with patch("rag._get_es", return_value=_mock_es(count=5, hits=hits)), \
         patch("rag._embed", return_value=[[0.1] * 10]):
        import rag
        result = rag.search("예금 금리", "fake-key")
    assert "참고 문서" in result
    assert "KB국민은행" in result


def test_list_documents_empty():
    """문서가 없으면 빈 리스트를 반환한다."""
    es = _mock_es()
    es.search.return_value = {"aggregations": {"names": {"buckets": []}}}
    with patch("rag._get_es", return_value=es):
        import rag
        result = rag.list_documents()
    assert result == []


def test_list_documents_returns_sorted():
    """인덱싱된 문서 목록을 알파벳순으로 반환한다."""
    es = _mock_es()
    es.search.return_value = {"aggregations": {"names": {"buckets": [
        {"key": "FSS_예금_신한은행_상품A"},
        {"key": "FSS_예금_KB국민은행_상품B"},
    ]}}}
    with patch("rag._get_es", return_value=es):
        import rag
        result = rag.list_documents()
    assert result == sorted(result)
