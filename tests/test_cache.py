"""cache 모듈 단위 테스트."""
import sys, os, time
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from unittest.mock import MagicMock, patch


def _make_chroma_collection(count=0, results=None):
    col = MagicMock()
    col.count.return_value = count
    col.query.return_value = results or {"distances": [[]], "metadatas": [[]]}
    return col


def test_store_and_redis_hit():
    """Redis에 저장된 항목은 캐시 히트로 반환된다."""
    store = {}

    mock_redis = MagicMock()
    mock_redis.get.side_effect = lambda k: store.get(k)
    mock_redis.setex.side_effect = lambda k, ttl, v: store.update({k: v})

    mock_col = _make_chroma_collection()

    with patch("cache._get_redis", return_value=mock_redis), \
         patch("cache._get_collection", return_value=mock_col), \
         patch("cache._embed", return_value=[0.1] * 10):
        import cache
        cache.store("테스트 질문", "테스트 답변", "fake-key")
        result = cache.check("테스트 질문", "fake-key", threshold=0.08)

    assert result == "테스트 답변"


def test_clear_resets_stats():
    """clear() 호출 후 Redis 항목이 삭제된다."""
    mock_redis = MagicMock()
    mock_redis.scan_iter.return_value = iter(["cache:abc", "cache:def"])

    mock_client = MagicMock()
    mock_client.delete_collection.return_value = None

    with patch("cache._get_redis", return_value=mock_redis), \
         patch("cache._get_client", return_value=mock_client):
        import cache
        cache.clear()

    assert mock_redis.delete.call_count == 2
    mock_client.delete_collection.assert_called_once()


def test_chroma_ttl_expired():
    """ChromaDB 항목이 24시간 초과 시 캐시 미스로 처리된다."""
    old_ts = time.time() - 86401  # 24시간 + 1초 경과

    mock_redis = MagicMock()
    mock_redis.get.return_value = None  # Redis 미스

    mock_col = _make_chroma_collection(
        count=1,
        results={
            "distances": [[0.05]],
            "metadatas": [[{"answer": "오래된 답변", "timestamp": old_ts}]],
        },
    )

    with patch("cache._get_redis", return_value=mock_redis), \
         patch("cache._get_collection", return_value=mock_col), \
         patch("cache._embed", return_value=[0.1] * 10):
        import cache
        result = cache.check("오래된 질문", "fake-key", threshold=0.08)

    assert result is None


def test_stats_returns_counts():
    """stats()는 Redis와 ChromaDB 항목 수를 반환한다."""
    mock_redis = MagicMock()
    mock_redis.scan_iter.return_value = iter(["cache:a", "cache:b", "cache:c"])

    mock_col = _make_chroma_collection(count=7)

    with patch("cache._get_redis", return_value=mock_redis), \
         patch("cache._get_collection", return_value=mock_col):
        import cache
        result = cache.stats()

    assert result["redis"] == 3
    assert result["chromadb"] == 7
