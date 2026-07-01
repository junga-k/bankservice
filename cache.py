"""시맨틱 캐시: 유사 쿼리의 LLM 응답을 재사용한다.

캐시 정책:
  - Redis L1: 정확 일치, TTL 30분, maxmemory 256mb + allkeys-lfu
  - ChromaDB L2: 코사인 유사도 < 0.08, 타임스탬프 기반 24시간 TTL
  - 재인덱싱 시 자동 초기화, Refresh-on-hit 적용 안 함

공개 API:
    check(query, openai_key, threshold=None) -> str | None
    store(query, answer, openai_key) -> None
    clear() -> None
    stats() -> dict
"""

from __future__ import annotations

import time
import uuid

import streamlit as st

_STORE_PATH = "rag_store"
_COLLECTION = "semantic_cache"
_DEFAULT_THRESHOLD = 0.08       # 강화된 임계값 (기존 0.12)
_CHROMA_TTL = 86400             # ChromaDB 24시간 TTL
_EMBED_MODEL = "text-embedding-3-small"

_REDIS_HOST   = "localhost"
_REDIS_PORT   = 6379
_REDIS_TTL    = 1800            # 30분 (기존 3600)
_REDIS_PREFIX = "cache:"


@st.cache_resource
def _get_redis():
    import redis as _redis
    import config as _cfg
    cfg = _cfg.load()
    try:
        r = _redis.Redis(
            host=cfg.get("redis_host", _REDIS_HOST),
            port=int(cfg.get("redis_port", _REDIS_PORT)),
            decode_responses=True,
        )
        r.ping()
        # maxmemory 정책 자동 적용
        r.config_set("maxmemory", "256mb")
        r.config_set("maxmemory-policy", "allkeys-lfu")
        return r
    except Exception:
        return None   # Redis 미실행 시 None → ChromaDB L2만 동작


@st.cache_resource
def _get_client():
    import chromadb
    return chromadb.PersistentClient(path=_STORE_PATH)


def _get_collection():
    return _get_client().get_or_create_collection(
        name=_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def _embed(text: str, openai_key: str) -> list[float]:
    from openai import OpenAI
    resp = OpenAI(api_key=openai_key).embeddings.create(
        model=_EMBED_MODEL,
        input=[text],
    )
    return resp.data[0].embedding


def _tracer():
    try:
        from opentelemetry import trace
        return trace.get_tracer("cache")
    except Exception:
        return None


def check(query: str, openai_key: str, threshold: float | None = None) -> str | None:
    """캐시 히트 시 저장된 답변 반환, 미스 시 None."""
    tracer = _tracer()
    span_ctx = None
    if tracer:
        try:
            from opentelemetry.trace import SpanKind
            from openinference.semconv.trace import SpanAttributes
            span_ctx = tracer.start_span("cache.check", kind=SpanKind.INTERNAL)
            span_ctx.set_attribute(SpanAttributes.INPUT_VALUE, query)
        except Exception:
            span_ctx = None

    try:
        if threshold is None:
            import config as _cfg
            threshold = float(_cfg.load().get("cache_threshold", _DEFAULT_THRESHOLD))

        import hashlib, json
        _redis_key = _REDIS_PREFIX + hashlib.sha256(query.encode()).hexdigest()

        # L1: Redis 정확 일치
        r = _get_redis()
        if r:
            cached = r.get(_redis_key)
            if cached:
                if span_ctx:
                    span_ctx.set_attribute("cache.hit", True)
                    span_ctx.set_attribute("cache.layer", "redis")
                return json.loads(cached)["answer"]

        # L2: ChromaDB 유사도 검색
        col = _get_collection()
        if col.count() == 0:
            if span_ctx:
                span_ctx.set_attribute("cache.hit", False)
                span_ctx.set_attribute("cache.reason", "empty")
            return None

        q_emb = _embed(query, openai_key)
        results = col.query(
            query_embeddings=[q_emb],
            n_results=1,
            include=["metadatas", "distances"],
        )
        distances = results.get("distances", [[]])[0]
        metas = results.get("metadatas", [[]])[0]

        if distances and distances[0] < threshold:
            meta = metas[0]
            # 타임스탬프 기반 24시간 TTL 확인
            if time.time() - meta.get("timestamp", 0) > _CHROMA_TTL:
                if span_ctx:
                    span_ctx.set_attribute("cache.hit", False)
                    span_ctx.set_attribute("cache.reason", "chroma_expired")
                return None
            answer = meta.get("answer")
            if span_ctx:
                from openinference.semconv.trace import SpanAttributes
                span_ctx.set_attribute("cache.hit", True)
                span_ctx.set_attribute("cache.layer", "chromadb")
                span_ctx.set_attribute("cache.distance", distances[0])
                span_ctx.set_attribute(SpanAttributes.OUTPUT_VALUE, answer or "")
                from opentelemetry.trace import StatusCode
                span_ctx.set_status(StatusCode.OK)
            return answer

        if span_ctx:
            span_ctx.set_attribute("cache.hit", False)
            if distances:
                span_ctx.set_attribute("cache.nearest_distance", distances[0])
            from opentelemetry.trace import StatusCode
            span_ctx.set_status(StatusCode.OK)
        return None
    except Exception as e:
        if span_ctx:
            span_ctx.record_exception(e)
            from opentelemetry.trace import StatusCode
            span_ctx.set_status(StatusCode.ERROR)
        raise
    finally:
        if span_ctx:
            span_ctx.end()


def store(query: str, answer: str, openai_key: str) -> None:
    """쿼리-답변 쌍을 캐시에 저장한다."""
    tracer = _tracer()
    span_ctx = None
    if tracer:
        try:
            from opentelemetry.trace import SpanKind
            from openinference.semconv.trace import SpanAttributes
            span_ctx = tracer.start_span("cache.store", kind=SpanKind.INTERNAL)
            span_ctx.set_attribute(SpanAttributes.INPUT_VALUE, query)
        except Exception:
            span_ctx = None

    try:
        import hashlib, json, config as _cfg
        _redis_key = _REDIS_PREFIX + hashlib.sha256(query.encode()).hexdigest()
        ttl = int(_cfg.load().get("redis_ttl", _REDIS_TTL))
        r = _get_redis()
        if r:
            r.setex(_redis_key, ttl, json.dumps({"answer": answer}))

        col = _get_collection()
        q_emb = _embed(query, openai_key)
        col.add(
            ids=[uuid.uuid4().hex],
            embeddings=[q_emb],
            documents=[query],
            metadatas=[{"query": query, "answer": answer, "timestamp": time.time()}],
        )
        if span_ctx:
            from opentelemetry.trace import StatusCode
            span_ctx.set_status(StatusCode.OK)
    except Exception as e:
        if span_ctx:
            span_ctx.record_exception(e)
            from opentelemetry.trace import StatusCode
            span_ctx.set_status(StatusCode.ERROR)
        raise
    finally:
        if span_ctx:
            span_ctx.end()


def clear() -> None:
    """캐시 전체 삭제 (Redis L1 + ChromaDB L2)."""
    r = _get_redis()
    if r:
        for k in r.scan_iter(f"{_REDIS_PREFIX}*"):
            r.delete(k)
    client = _get_client()
    try:
        client.delete_collection(_COLLECTION)
    except Exception:
        pass


def stats() -> dict:
    """Redis/ChromaDB 캐시 현황 반환."""
    r = _get_redis()
    redis_count = len(list(r.scan_iter(f"{_REDIS_PREFIX}*"))) if r else 0
    try:
        chroma_count = _get_collection().count()
    except Exception:
        chroma_count = 0
    return {"redis": redis_count, "chromadb": chroma_count}
