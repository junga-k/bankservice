"""인프라 실시간 지표 (Backoffice 대시보드용).

성능관리 탭의 backend/health.py 와 별개다. health.py 는 ok/warn/down 상태만
보여주는 단순 헬스체크이고, 이 모듈은 대시보드에 실제 수치(Kafka lag, ES 문서수,
Phoenix 트레이스/LLM/RAG/캐시 집계)를 노출한다.

각 함수는 독립적으로 try/except 로 감싸 하나가 죽어도 나머지는 정상 반환한다.
외부 시스템이 없으면 status="down" + 안내 detail 을 돌려주며 예외를 전파하지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

PHOENIX_URL = "http://localhost:6006"
PHOENIX_PROJECT = "ai-chat"
ES_HOST = "http://localhost:9200"
ES_INDEX = "rag_documents"


def kafka_metrics() -> dict:
    """transfer-worker 그룹의 컨슈머 lag 을 그룹에 join 하지 않고 계산한다.

    - 커밋 오프셋: KafkaAdminClient.list_group_offsets (admin 전용, 그룹 join 안 함)
    - 로그엔드 오프셋: 임시 KafkaConsumer 를 .assign() 으로 수동 할당(.subscribe() 아님)
      → 코디네이터 join/rebalance 없이 end_offsets 만 조회
    lag = Σ max(0, end_offset - committed_offset)
    """
    admin = consumer = None
    try:
        from kafka import KafkaConsumer, TopicPartition
        from kafka.admin import KafkaAdminClient

        from backend.kafka_io import BROKER, TOPIC_TRANSFER

        admin = KafkaAdminClient(bootstrap_servers=BROKER, request_timeout_ms=1500)
        topics = admin.list_topics()
        topic_count = len(topics)
        if TOPIC_TRANSFER not in topics:
            return {
                "status": "warn",
                "detail": f"브로커 {BROKER} 연결됨 · 토픽 '{TOPIC_TRANSFER}' 없음",
                "topic_count": topic_count,
                "lag": 0,
            }

        consumer = KafkaConsumer(
            bootstrap_servers=BROKER,
            enable_auto_commit=False,
            consumer_timeout_ms=1500,
            request_timeout_ms=2000,
        )
        parts = consumer.partitions_for_topic(TOPIC_TRANSFER) or set()
        tps = [TopicPartition(TOPIC_TRANSFER, p) for p in parts]
        consumer.assign(tps)
        end_offsets = consumer.end_offsets(tps) if tps else {}

        committed = admin.list_group_offsets("transfer-worker")  # {TopicPartition: OffsetAndMetadata}

        lag = 0
        for tp in tps:
            end = end_offsets.get(tp, 0)
            meta = committed.get(tp)
            com = meta.offset if meta else 0
            lag += max(0, end - com)

        return {
            "status": "ok",
            "detail": f"브로커 {BROKER} 연결됨 · 미처리 메시지 {lag:,}건",
            "topic_count": topic_count,
            "lag": lag,
        }
    except Exception as e:
        return {
            "status": "down",
            "detail": f"연결할 수 없습니다: {e}",
            "topic_count": 0,
            "lag": 0,
        }
    finally:
        try:
            if consumer is not None:
                consumer.close()
        except Exception:
            pass
        try:
            if admin is not None:
                admin.close()
        except Exception:
            pass


def elasticsearch_metrics() -> dict:
    """ES 클러스터 상태 + rag_documents 인덱스 문서수. health.py 의 check 와 별개로 재조회."""
    try:
        import config as _cfg
        from elasticsearch import Elasticsearch

        es = Elasticsearch(_cfg.load().get("es_host", ES_HOST), request_timeout=2)
        if not es.ping():
            raise ConnectionError("ping 실패")
        h = es.cluster.health()
        cluster_status = h.get("status", "unknown")
        node_count = h.get("number_of_nodes", 0)
        active_shards = h.get("active_shards", 0)
        try:
            doc_count = es.count(index=ES_INDEX).get("count", 0)
        except Exception:
            doc_count = 0
        return {
            "status": "ok" if cluster_status in ("green", "yellow") else "warn",
            "detail": f"클러스터 {cluster_status} · 노드 {node_count} · 문서 {doc_count:,}건",
            "cluster_status": cluster_status,
            "node_count": node_count,
            "active_shards": active_shards,
            "doc_count": doc_count,
        }
    except Exception:
        return {
            "status": "down",
            "detail": f"연결할 수 없습니다. Elasticsearch가 {ES_HOST} 에서 실행 중인지 확인하세요.",
            "cluster_status": "unknown",
            "node_count": 0,
            "active_shards": 0,
            "doc_count": 0,
        }


def scheduled_poller_metrics(started: bool, last_run: float, pending_count: int) -> dict:
    """예약/지연 이체 폴러의 헬스. 인자는 backend.app 의 get_poller_status()·대기건수에서 주입.

    - down: 미기동
    - warn: 마지막 실행이 45초를 초과(정체 의심) — 폴링 주기 15초의 3배
    - ok:   45초 이내 정상 순환
    """
    import time as _time

    if not started or last_run <= 0:
        return {
            "status": "down",
            "detail": "폴러가 기동되지 않았습니다. 예약/지연 이체가 실행되지 않습니다.",
            "pending": pending_count,
            "age_seconds": None,
        }
    age = _time.time() - last_run
    status = "ok" if age <= 45 else "warn"
    detail = f"마지막 실행 {int(age)}초 전 · 대기 {pending_count}건"
    if status == "warn":
        detail = f"정체 의심 — {detail}"
    return {"status": status, "detail": detail, "pending": pending_count, "age_seconds": int(age)}


def phoenix_metrics() -> dict:
    """Phoenix 에 기록된 최근 24시간 span 을 읽어 LLM/RAG/캐시 사용량을 집계한다.

    in-process 카운터가 없으므로 Phoenix(OTEL 수신처)가 LLM/RAG 활동의 유일한
    실시간 신호원이다. span_kind/name 은 서버 버전에 따라 쿼리 필터가 막힐 수 있어
    start_time/limit 만 넘기고 파이썬에서 필터링한다.
    """
    try:
        from phoenix.client import Client

        client = Client(base_url=PHOENIX_URL)
        start = datetime.now(timezone.utc) - timedelta(hours=24)
        spans = client.spans.get_spans(
            project_identifier=PHOENIX_PROJECT,
            start_time=start,
            limit=500,
            timeout=3,
        )

        trace_count = len(spans)
        llm_latencies: list[float] = []
        rag_search_count = 0
        cache_total = 0
        cache_hits = 0

        for sp in spans:
            kind = sp.get("span_kind", "")
            name = sp.get("name", "")
            attrs = sp.get("attributes") or {}

            if kind == "LLM":
                lat = _span_latency_ms(sp)
                if lat is not None:
                    llm_latencies.append(lat)
            if name == "rag.search":
                rag_search_count += 1
            if name == "cache.check":
                cache_total += 1
                if attrs.get("cache.hit") is True:
                    cache_hits += 1

        llm_request_count = len(llm_latencies)
        llm_avg_latency_ms = round(sum(llm_latencies) / llm_request_count) if llm_request_count else 0
        cache_hit_rate = round(cache_hits / cache_total * 100) if cache_total else 0

        return {
            "status": "ok",
            "detail": f"{PHOENIX_URL} 연결됨 · 최근 24시간 트레이스 {trace_count:,}건",
            "trace_count_24h": trace_count,
            "llm_request_count": llm_request_count,
            "llm_avg_latency_ms": llm_avg_latency_ms,
            "rag_search_count": rag_search_count,
            "cache_hit_rate": cache_hit_rate,
        }
    except Exception:
        return {
            "status": "down",
            "detail": "연결할 수 없습니다. `.venv/bin/phoenix serve`로 기동하세요.",
            "trace_count_24h": 0,
            "llm_request_count": 0,
            "llm_avg_latency_ms": 0,
            "rag_search_count": 0,
            "cache_hit_rate": 0,
        }


def _span_latency_ms(span: dict) -> float | None:
    """span 의 start_time/end_time(ISO 8601) 차이를 ms 로. 파싱 실패 시 None."""
    try:
        start = datetime.fromisoformat(span["start_time"])
        end = datetime.fromisoformat(span["end_time"])
        return (end - start).total_seconds() * 1000
    except Exception:
        return None
