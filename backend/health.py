"""시스템 상태 점검 (Backoffice 성능관리용).

측정 가능한 시스템이 늘어나면 check_*() 함수를 추가하고 check_all()의
리스트에 넣기만 하면 된다 — 프런트는 이 리스트 길이에 맞춰 카드를 그린다.
"""
from __future__ import annotations

import concurrent.futures
import urllib.request

PHOENIX_URL = "http://localhost:6006"
ES_HOST = "http://localhost:9200"
ES_INDEX = "rag_documents"

# Kafka/Elasticsearch 클라이언트 라이브러리는 브로커가 죽어 있으면 자체 재시도 때문에
# 요청 timeout 파라미터를 줘도 실제로는 수십 초씩 걸릴 수 있다(kafka-python의 부트스트랩
# 재시도가 대표적). 라이브러리 타임아웃을 믿지 않고, 별도 스레드에서 실행해 이 시간이
# 지나면 그냥 "연결안됨"으로 포기한다 — 백그라운드 스레드는 계속 돌다 알아서 끝난다.
_CHECK_TIMEOUT_SEC = 2.0
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="healthcheck")


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


def _run_with_timeout(fn, name: str) -> dict:
    future = _executor.submit(fn)
    try:
        return future.result(timeout=_CHECK_TIMEOUT_SEC)
    except concurrent.futures.TimeoutError:
        return {
            "name": name,
            "status": "down",
            "detail": f"응답이 {_CHECK_TIMEOUT_SEC:.0f}초 넘게 걸려 시간 초과 처리했습니다.",
        }
    except Exception as e:
        return {"name": name, "status": "down", "detail": f"확인 중 오류: {e}"}


def check_all() -> list[dict]:
    return [
        _run_with_timeout(check_phoenix, "Phoenix (LLM 추적)"),
        _run_with_timeout(check_kafka, "Kafka"),
        _run_with_timeout(check_elasticsearch, "Elasticsearch"),
    ]
