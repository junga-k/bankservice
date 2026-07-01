"""RAG 모듈: 문서 청킹 → OpenAI 임베딩 → Elasticsearch 하이브리드 검색.

BM25(키워드) + kNN(벡터) 하이브리드 검색으로 정확도 향상.

공개 API:
    index_document(name, text, openai_key) -> int   # 청크 수 반환
    search(query, openai_key, top_k=5) -> str       # 관련 청크 문자열
    list_documents() -> list[str]
    remove_document(name) -> None
"""

from __future__ import annotations

import streamlit as st

ES_HOST = "http://localhost:9200"
ES_INDEX = "rag_documents"
EMBED_DIMS = 1536   # text-embedding-3-small
EMBED_MODEL = "text-embedding-3-small"

_CHUNK_SIZE = 900
_CHUNK_OVERLAP = 100

# 직전 search()가 조회한 doc_name 목록(이용 통계 로깅용). 중복 제거·순서 보존.
_last_doc_names: list[str] = []


def last_doc_names() -> list[str]:
    """직전 search() 호출이 조회한 문서명 목록을 반환한다(이용 통계 로깅용)."""
    return list(_last_doc_names)


@st.cache_resource
def _get_es():
    import config as _cfg
    from elasticsearch import Elasticsearch
    host = _cfg.load().get("es_host", ES_HOST)
    return Elasticsearch(host)


def _ensure_index(es) -> None:
    """인덱스 없으면 생성. text(BM25) + embedding(kNN) 필드."""
    if not es.indices.exists(index=ES_INDEX):
        es.indices.create(index=ES_INDEX, mappings={"properties": {
            "text":      {"type": "text", "analyzer": "standard"},
            "embedding": {"type": "dense_vector", "dims": EMBED_DIMS,
                          "index": True, "similarity": "cosine"},
            "doc_name":  {"type": "keyword"},
            "chunk_idx": {"type": "integer"},
        }})


def _embed(texts: list[str], openai_key: str) -> list[list[float]]:
    from openai import OpenAI
    resp = OpenAI(api_key=openai_key).embeddings.create(
        model=EMBED_MODEL, input=texts
    )
    return [r.embedding for r in resp.data]


def _chunk(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + _CHUNK_SIZE])
        start += _CHUNK_SIZE - _CHUNK_OVERLAP
    return chunks


def _tracer():
    try:
        from opentelemetry import trace
        return trace.get_tracer("rag")
    except Exception:
        return None


def index_document(name: str, text: str, openai_key: str) -> int:
    """문서를 청킹·임베딩 후 ES에 저장. 같은 이름이면 기존 청크 먼저 삭제."""
    tracer = _tracer()
    span_ctx = None
    if tracer:
        try:
            from opentelemetry.trace import SpanKind
            span_ctx = tracer.start_span("rag.index_document", kind=SpanKind.INTERNAL)
            span_ctx.set_attribute("rag.document_name", name)
            span_ctx.set_attribute("rag.text_length", len(text))
            span_ctx.set_attribute("llm.model_name", EMBED_MODEL)
        except Exception:
            span_ctx = None

    try:
        es = _get_es()
        _ensure_index(es)

        # 기존 청크 삭제
        es.delete_by_query(
            index=ES_INDEX,
            query={"term": {"doc_name": name}},
            refresh=True,
        )

        chunks = _chunk(text)
        if not chunks:
            return 0

        embeddings = _embed(chunks, openai_key)

        # bulk 적재
        from elasticsearch.helpers import bulk
        actions = [
            {
                "_index": ES_INDEX,
                "_id": f"{name}__{i}",
                "_source": {
                    "text": chunk,
                    "embedding": emb,
                    "doc_name": name,
                    "chunk_idx": i,
                },
            }
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings))
        ]
        bulk(es, actions, refresh=True)

        if span_ctx:
            span_ctx.set_attribute("rag.chunk_count", len(chunks))
            from opentelemetry.trace import StatusCode
            span_ctx.set_status(StatusCode.OK)
        return len(chunks)

    except Exception as e:
        if span_ctx:
            span_ctx.record_exception(e)
            from opentelemetry.trace import StatusCode
            span_ctx.set_status(StatusCode.ERROR)
        raise
    finally:
        if span_ctx:
            span_ctx.end()


def search(query: str, openai_key: str, top_k: int | None = None) -> str:
    """BM25 + kNN 하이브리드 검색으로 관련 청크를 반환한다."""
    tracer = _tracer()
    span_ctx = None
    if tracer:
        try:
            from opentelemetry.trace import SpanKind
            from openinference.semconv.trace import SpanAttributes
            span_ctx = tracer.start_span("rag.search", kind=SpanKind.INTERNAL)
            span_ctx.set_attribute(SpanAttributes.INPUT_VALUE, query)
            span_ctx.set_attribute("rag.top_k", top_k)
            span_ctx.set_attribute("rag.mode", "hybrid")
        except Exception:
            span_ctx = None

    try:
        if top_k is None:
            import config as _cfg
            top_k = _cfg.load().get("rag_top_k", 10)

        es = _get_es()
        _ensure_index(es)

        # 인덱스가 비어 있으면 빠르게 반환
        count = es.count(index=ES_INDEX).get("count", 0)
        if count == 0:
            return ""

        q_emb = _embed([query], openai_key)[0]

        # 하이브리드 검색: BM25(match) + kNN 점수 자동 합산
        resp = es.search(
            index=ES_INDEX,
            size=top_k,
            query={"match": {"text": query}},
            knn={
                "field": "embedding",
                "query_vector": q_emb,
                "k": top_k,
                "num_candidates": top_k * 10,
            },
        )
        hits = resp["hits"]["hits"]
        # 조회된 문서명 기록(이용 통계용) — 중복 제거·순서 보존
        global _last_doc_names
        _seen: set[str] = set()
        _last_doc_names = []
        for hit in hits:
            dn = hit["_source"].get("doc_name", "")
            if dn and dn not in _seen:
                _seen.add(dn)
                _last_doc_names.append(dn)

        if not hits:
            return ""

        parts = []
        for hit in hits:
            src = hit["_source"]
            parts.append(f"[출처: {src['doc_name']}]\n{src['text']}")
        output = "=== 참고 문서 ===\n" + "\n\n".join(parts) + "\n================"

        if span_ctx:
            from openinference.semconv.trace import SpanAttributes
            span_ctx.set_attribute(SpanAttributes.OUTPUT_VALUE, output)
            span_ctx.set_attribute("rag.retrieved_chunks", len(hits))
            from opentelemetry.trace import StatusCode
            span_ctx.set_status(StatusCode.OK)
        return output

    except Exception as e:
        if span_ctx:
            span_ctx.record_exception(e)
            from opentelemetry.trace import StatusCode
            span_ctx.set_status(StatusCode.ERROR)
        raise
    finally:
        if span_ctx:
            span_ctx.end()


def list_documents() -> list[str]:
    try:
        es = _get_es()
        _ensure_index(es)
        agg = es.search(
            index=ES_INDEX,
            size=0,
            aggregations={"names": {"terms": {"field": "doc_name", "size": 1000}}},
        )
        buckets = agg["aggregations"]["names"]["buckets"]
        return sorted(b["key"] for b in buckets)
    except Exception:
        return []


def remove_document(name: str) -> None:
    try:
        es = _get_es()
        es.delete_by_query(
            index=ES_INDEX,
            query={"term": {"doc_name": name}},
            refresh=True,
        )
    except Exception:
        pass
