"""시스템 상태 점검 (Backoffice 성능관리용).

측정 가능한 시스템이 늘어나면 check_*() 함수를 추가하고 check_all()의
리스트에 넣기만 하면 된다 — 프런트는 이 리스트 길이에 맞춰 카드를 그린다.
"""
from __future__ import annotations

import urllib.request

PHOENIX_URL = "http://localhost:6006"
ES_HOST = "http://localhost:9200"
ES_INDEX = "rag_documents"


def check_phoenix() -> dict:
    """tracing.py:_is_phoenix_running()과 동일한 방식(healthz 핑)."""
    try:
        urllib.request.urlopen(f"{PHOENIX_URL}/healthz", timeout=1)
        return {"name": "Phoenix (LLM 추적)", "status": "ok", "detail": f"{PHOENIX_URL} 연결됨"}
    except Exception:
        return {
            "name": "Phoenix (LLM 추적)",
            "status": "down",
            "detail": "연결할 수 없습니다. `.venv/bin/phoenix serve`로 기동하세요.",
        }


def check_kafka() -> dict:
    try:
        from kafka.admin import KafkaAdminClient

        from backend.kafka_io import BROKER, TOPIC_TRANSFER

        admin = KafkaAdminClient(bootstrap_servers=BROKER, request_timeout_ms=1500)
        try:
            topics = admin.list_topics()
        finally:
            admin.close()
        topic_ok = TOPIC_TRANSFER in topics
        return {
            "name": "Kafka",
            "status": "ok" if topic_ok else "warn",
            "detail": f"브로커 {BROKER} 연결됨" + ("" if topic_ok else f" · 토픽 '{TOPIC_TRANSFER}' 없음"),
        }
    except Exception as e:
        return {"name": "Kafka", "status": "down", "detail": f"연결할 수 없습니다: {e}"}


def check_elasticsearch() -> dict:
    try:
        import config as _cfg
        from elasticsearch import Elasticsearch

        es = Elasticsearch(_cfg.load().get("es_host", ES_HOST))
        if not es.ping():
            raise ConnectionError("ping 실패")
        cluster_status = es.cluster.health().get("status", "unknown")
        try:
            count = es.count(index=ES_INDEX).get("count", 0)
        except Exception:
            count = 0
        return {
            "name": "Elasticsearch",
            "status": "ok" if cluster_status in ("green", "yellow") else "warn",
            "detail": f"클러스터 상태 {cluster_status} · 문서 {count:,}건",
        }
    except Exception:
        return {
            "name": "Elasticsearch",
            "status": "down",
            "detail": f"연결할 수 없습니다. Elasticsearch가 {ES_HOST} 에서 실행 중인지 확인하세요.",
        }


def check_all() -> list[dict]:
    return [check_phoenix(), check_kafka(), check_elasticsearch()]
